-- Synthetic sales data: 24 months, 6 regions, 8 product categories, about 200k order lines with seasonality.
-- Fixed seed, so every run produces the same numbers.
SELECT setseed(0.42);

CREATE SCHEMA sales;

CREATE TABLE sales.orders (
  order_id     bigint PRIMARY KEY,
  order_date   date NOT NULL,
  region       text NOT NULL,
  category     text NOT NULL,
  quantity     int  NOT NULL,
  unit_price   numeric(10,2) NOT NULL
);

-- Regions and categories have different weights, and the end of the year sells more, so the charts look like a business.
WITH d AS (
  SELECT g,
         date '2024-10-01' + (random() * 729)::int AS day,
         random() AS r_region, random() AS r_cat, random() AS keep
  FROM generate_series(1, 260000) g
)
INSERT INTO sales.orders
SELECT g, day,
       CASE WHEN r_region < .38 THEN 'Southeast' WHEN r_region < .58 THEN 'Northeast' WHEN r_region < .73 THEN 'South'
            WHEN r_region < .85 THEN 'Online' WHEN r_region < .94 THEN 'Centre-West' ELSE 'North' END,
       CASE WHEN r_cat < .27 THEN 'Grocery' WHEN r_cat < .45 THEN 'Beverages' WHEN r_cat < .58 THEN 'Cleaning'
            WHEN r_cat < .70 THEN 'Personal care' WHEN r_cat < .80 THEN 'Bakery' WHEN r_cat < .89 THEN 'Frozen'
            WHEN r_cat < .95 THEN 'Household' ELSE 'Pet' END,
       1 + floor(random() * 6)::int,
       round((2 + random() * 48)::numeric, 2)
FROM d
-- seasonality: keep fewer orders in the first months of the year, all of them in November and December
WHERE keep < CASE extract(month FROM day) WHEN 11 THEN 1 WHEN 12 THEN 1 WHEN 1 THEN .62 WHEN 2 THEN .6 ELSE .78 END;

CREATE INDEX ON sales.orders (order_date);

-- Charts read small views, never the raw table.
CREATE SCHEMA bi;
CREATE MATERIALIZED VIEW bi.revenue_by_month AS
  SELECT date_trunc('month', order_date)::date AS month, sum(quantity * unit_price)::numeric(14,2) AS revenue, count(*) AS orders
  FROM sales.orders GROUP BY 1 ORDER BY 1;
CREATE MATERIALIZED VIEW bi.revenue_by_region AS
  SELECT region, sum(quantity * unit_price)::numeric(14,2) AS revenue FROM sales.orders GROUP BY 1 ORDER BY 2 DESC;
CREATE MATERIALIZED VIEW bi.revenue_by_category AS
  SELECT category, sum(quantity * unit_price)::numeric(14,2) AS revenue, avg(unit_price)::numeric(10,2) AS avg_price
  FROM sales.orders GROUP BY 1 ORDER BY 2 DESC;
