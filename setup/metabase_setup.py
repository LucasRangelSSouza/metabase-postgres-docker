"""Configures a fresh Metabase from code, idempotently, and times each step.

1. first-run setup (admin account from the environment);
2. registers the warehouse with the read-only bi_reader role, limited to the bi schema;
3. waits for the schema sync;
4. creates three SQL questions over the bi views;
5. builds a dashboard and enables its public link;
6. creates a "Viewers" group that can open dashboards and use the query builder but cannot write SQL or edit;
7. checks the result from the outside and writes timings.json.

Standard library only. Secrets come from the environment and are never printed.
  set -a; . ./.env; set +a; python setup/metabase_setup.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MB_URL = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
DB_NAME = "Warehouse (read-only)"
DASHBOARD = "Sales overview"
GROUP = "Viewers"
CARDS = [
    ("Revenue by month", "line", "SELECT month, revenue FROM bi.revenue_by_month ORDER BY month"),
    ("Revenue by region", "bar", "SELECT region, revenue FROM bi.revenue_by_region ORDER BY revenue DESC"),
    ("Revenue by category", "row", "SELECT category, revenue FROM bi.revenue_by_category ORDER BY revenue DESC"),
]
timings: dict[str, float] = {}


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing environment variable {name}")
    return value


class Metabase:
    def __init__(self) -> None:
        self.session: str | None = None

    def call(self, method: str, path: str, body=None, auth: bool = True):
        headers = {"Content-Type": "application/json"}
        if auth and self.session:
            headers["X-Metabase-Session"] = self.session
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(MB_URL + path, data=data, method=method, headers=headers), timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except HTTPError as e:
            raise SystemExit(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from None


def step(name):
    def wrap(fn):
        def run(*a, **k):
            start = time.perf_counter()
            result = fn(*a, **k)
            timings[name] = round(time.perf_counter() - start, 2)
            print(f"{name}: {timings[name]} s")
            return result
        return run
    return wrap


@step("wait_healthy")
def wait_healthy(timeout_s: int = 600) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urlopen(MB_URL + "/api/health", timeout=5) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return
        except (URLError, HTTPError, OSError, ValueError):
            pass
        time.sleep(3)
    raise SystemExit("Metabase did not become healthy")


@step("first_run_setup")
def setup(mb: Metabase) -> None:
    props = mb.call("GET", "/api/session/properties", auth=False)
    email, password = env("METABASE_ADMIN_EMAIL"), env("METABASE_ADMIN_PASSWORD")
    if not props.get("has-user-setup"):
        mb.session = mb.call("POST", "/api/setup", {
            "token": props["setup-token"],
            "user": {"email": email, "password": password, "first_name": "Admin", "last_name": "BI", "site_name": "BI"},
            "prefs": {"site_name": "BI", "site_locale": "en", "allow_tracking": False},
        })["id"]
    else:
        mb.session = mb.call("POST", "/api/session", {"username": email, "password": password})["id"]


@step("add_database_and_sync")
def ensure_database(mb: Metabase) -> int:
    listing = mb.call("GET", "/api/database")
    dbs = listing["data"] if isinstance(listing, dict) else listing
    db = next((d for d in dbs if d["name"] == DB_NAME), None)
    if db is None:
        db = mb.call("POST", "/api/database", {
            "engine": "postgres", "name": DB_NAME, "is_full_sync": True,
            "details": {"host": os.environ.get("WAREHOUSE_HOST", "warehouse"), "port": 5432, "dbname": "warehouse",
                        "user": "bi_reader", "password": env("BI_READER_PASSWORD"), "ssl": False,
                        "schema-filters-type": "inclusion", "schema-filters-patterns": "bi"},
        })
    mb.call("POST", f"/api/database/{db['id']}/sync_schema", {})
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        tables = mb.call("GET", f"/api/database/{db['id']}/metadata").get("tables", [])
        if len([t for t in tables if t.get("schema") == "bi" and t.get("fields")]) >= 3:
            return db["id"]
        time.sleep(3)
    raise SystemExit("sync did not expose the bi views")


@step("create_cards_and_dashboard")
def ensure_dashboard(mb: Metabase, db_id: int) -> int:
    existing = mb.call("GET", "/api/card?f=all")
    ids = []
    for name, display, sql in CARDS:
        card = next((c for c in existing if c["name"] == name and not c.get("archived")), None)
        body = {"name": name, "display": display, "visualization_settings": {},
                "dataset_query": {"type": "native", "database": db_id, "native": {"query": sql}}}
        card = mb.call("PUT", f"/api/card/{card['id']}", body) if card else mb.call("POST", "/api/card", body)
        ids.append(card["id"])
    dash = next((d for d in mb.call("GET", "/api/dashboard?f=all") if d["name"] == DASHBOARD and not d.get("archived")), None)
    dash = dash or mb.call("POST", "/api/dashboard", {"name": DASHBOARD, "description": "Synthetic data; numbers are illustrative."})
    layout = [(0, 0, 24, 6), (6, 0, 12, 7), (6, 12, 12, 7)]
    mb.call("PUT", f"/api/dashboard/{dash['id']}", {"dashcards": [
        {"id": -(i + 1), "card_id": cid, "row": r, "col": c, "size_x": w, "size_y": h,
         "parameter_mappings": [], "visualization_settings": {}} for i, (cid, (r, c, w, h)) in enumerate(zip(ids, layout))]})
    return dash["id"]


@step("public_link")
def public_link(mb: Metabase, dash_id: int) -> str:
    return mb.call("POST", f"/api/dashboard/{dash_id}/public_link", {})["uuid"]


@step("viewer_group")
def viewer_group(mb: Metabase, db_id: int) -> int:
    groups = mb.call("GET", "/api/permissions/group")
    group = next((g for g in groups if g["name"] == GROUP), None) or mb.call("POST", "/api/permissions/group", {"name": GROUP})
    all_users = next(g["id"] for g in groups if g["name"] == "All Users")
    graph = mb.call("GET", "/api/permissions/graph")
    # Viewers may explore the bi schema with the query builder; nobody but admins may write SQL.
    mine = {str(db_id): {"view-data": "unrestricted", "create-queries": {"bi": "query-builder"}}}
    nobody = {str(db_id): {"view-data": "unrestricted", "create-queries": "no"}}
    mb.call("PUT", "/api/permissions/graph", {"revision": graph["revision"],
                                              "groups": {str(group["id"]): mine, str(all_users): nobody}})
    cgraph = mb.call("GET", "/api/collection/graph")
    mb.call("PUT", "/api/collection/graph", {"revision": cgraph["revision"],
                                             "groups": {str(group["id"]): {"root": "read"}, str(all_users): {"root": "none"}}})
    return group["id"]


@step("checks")
def checks(uuid: str) -> dict:
    with urlopen(f"{MB_URL}/api/public/dashboard/{uuid}", timeout=30) as r:
        dash = json.loads(r.read())
    try:
        urlopen(f"{MB_URL}/api/user/current", timeout=10)
        anonymous = "open"
    except HTTPError as e:
        anonymous = e.code
    return {"public_dashboard_cards": len(dash.get("dashcards", [])), "anonymous_api": anonymous}


def main() -> int:
    mb = Metabase()
    wait_healthy()
    setup(mb)
    db_id = ensure_database(mb)
    dash_id = ensure_dashboard(mb, db_id)
    uuid = public_link(mb, dash_id)
    group_id = viewer_group(mb, db_id)
    result = checks(uuid)
    out = {"metabase": mb.call("GET", "/api/session/properties")["version"]["tag"], "timings_s": timings,
           "dashboard_id": dash_id, "viewer_group_id": group_id, "public_path": f"/public/dashboard/{uuid}", **result}
    json.dump(out, open("timings.json", "w"), indent=1)
    print(json.dumps(out, indent=1))
    ok = result["public_dashboard_cards"] == 3 and result["anonymous_api"] == 401
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
