#!/bin/sh
# A role that can read the bi views and nothing else, with a statement timeout so one chart cannot hog the database.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE bi_reader LOGIN PASSWORD '${BI_READER_PASSWORD}' CONNECTION LIMIT 10;
ALTER ROLE bi_reader SET statement_timeout = '60s';
ALTER ROLE bi_reader SET default_transaction_read_only = on;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA bi TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA bi TO bi_reader;
SQL
