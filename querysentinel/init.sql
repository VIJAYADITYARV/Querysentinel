-- =============================================
-- QuerySentinel — Database Initialisation
-- Runs automatically when container first starts
-- =============================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- =============================================
-- SAMPLE TABLES (for test app to query)
-- =============================================

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    email       VARCHAR(150) UNIQUE NOT NULL,
    country     VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    category    VARCHAR(100),
    price       NUMERIC(10, 2),
    stock       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    user_id     INT REFERENCES users(id),
    product_id  INT REFERENCES products(id),
    quantity    INT,
    total       NUMERIC(10, 2),
    status      VARCHAR(50) DEFAULT 'pending',
    ordered_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reviews (
    id          SERIAL PRIMARY KEY,
    user_id     INT REFERENCES users(id),
    product_id  INT REFERENCES products(id),
    rating      INT CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================
-- QUERY LOGS TABLE (where QuerySentinel stores)
-- =============================================

CREATE TABLE IF NOT EXISTS query_logs (
    id          BIGSERIAL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_sql     TEXT NOT NULL,
    total_cost  FLOAT,
    actual_rows BIGINT,
    node_type   VARCHAR(100),
    exec_ms     FLOAT,
    raw_plan    JSONB
);

-- Make it a TimescaleDB hypertable (time-partitioned automatically)
SELECT create_hypertable('query_logs', 'captured_at', if_not_exists => TRUE);

-- =============================================
-- SEED DATA — 500 users, 100 products, 1000 orders
-- Enough data to make queries interesting
-- =============================================

-- Insert 500 users
INSERT INTO users (name, email, country)
SELECT
    'User_' || i,
    'user' || i || '@example.com',
    (ARRAY['India', 'USA', 'UK', 'Germany', 'Japan', 'Brazil', 'Canada'])[1 + (i % 7)]
FROM generate_series(1, 500) AS s(i);

-- Insert 100 products
INSERT INTO products (name, category, price, stock)
SELECT
    'Product_' || i,
    (ARRAY['Electronics', 'Clothing', 'Books', 'Food', 'Sports', 'Toys'])[1 + (i % 6)],
    (random() * 990 + 10)::NUMERIC(10,2),
    (random() * 500)::INT
FROM generate_series(1, 100) AS s(i);

-- Insert 1000 orders
INSERT INTO orders (user_id, product_id, quantity, total, status)
SELECT
    (random() * 499 + 1)::INT,
    (random() * 99 + 1)::INT,
    (random() * 5 + 1)::INT,
    (random() * 500 + 10)::NUMERIC(10,2),
    (ARRAY['pending', 'shipped', 'delivered', 'cancelled'])[1 + (i % 4)]
FROM generate_series(1, 1000) AS s(i);

-- Insert 800 reviews
INSERT INTO reviews (user_id, product_id, rating, comment)
SELECT
    (random() * 499 + 1)::INT,
    (random() * 99 + 1)::INT,
    (random() * 4 + 1)::INT,
    'Review comment number ' || i
FROM generate_series(1, 800) AS s(i);

-- NOTE: No indexes on foreign keys intentionally
-- This makes some queries expensive — perfect for QuerySentinel to catch
