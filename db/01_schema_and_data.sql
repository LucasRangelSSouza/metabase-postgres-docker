-- Synthetic sales data: 24 months, 6 regions, 8 product categories, about 200k order lines.
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

INSERT INTO sales.orders
SELECT g,
       date '2024-10-01' + (random() * 729)::int,
       (ARRAY['North','Northeast','Centre-West','Southeast','South','Online'])[1 + floor(random() * 6)::int],
       (ARRAY['Grocery','Beverages','Cleaning','Personal care','Bakery','Frozen','Pet','Household'])[1 + floor(random() * 8)::int],
       1 + floor(random() * 6)::int,
       round((2 + random() * 48)::numeric, 2)
FROM generate_series(1, 200000) g;

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
