"""
QuerySentinel — Test Application
=================================
This app exists purely to generate real SQL queries
for the proxy to intercept and analyse.

5 routes, each generating a different query type:
  1. /users       — simple SELECT (fast, cheap)
  2. /orders      — JOIN query (medium cost)
  3. /reports     — aggregation with GROUP BY (medium-high cost)
  4. /search      — full table scan, no index (expensive)
  5. /summary     — nested subquery (most expensive)

Run: python testapp/app.py
"""

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify
import os

app = Flask(__name__)

# ─── Database connection ───────────────────────────────────────────────────────

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5433)),
    "dbname": os.getenv("DB_NAME", "querysentinel"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


def get_connection():
    """Get a fresh database connection."""
    return psycopg2.connect(**DB_CONFIG)


def run_query(sql, params=None):
    """Execute SQL and return results as list of dicts."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            results = cur.fetchall()
            return [dict(row) for row in results]
    finally:
        conn.close()


# ─── Route 1: Simple SELECT ────────────────────────────────────────────────────
# Query type : Sequential scan on small result set
# Expected cost : LOW (fast)
# What QuerySentinel learns: this is the "normal" baseline

@app.route("/users")
def get_users():
    """
    Simple SELECT with LIMIT.
    Fast query — index on primary key.
    QuerySentinel should score this as LOW COST.
    """
    sql = """
        SELECT id, name, email, country, created_at
        FROM users
        ORDER BY id
        LIMIT 20
    """
    results = run_query(sql)
    return jsonify({
        "route": "/users",
        "query_type": "simple_select",
        "count": len(results),
        "data": results
    })


# ─── Route 2: JOIN query ───────────────────────────────────────────────────────
# Query type : Hash join between orders and users and products
# Expected cost : MEDIUM
# What QuerySentinel learns: joins are more expensive than simple selects

@app.route("/orders")
def get_orders():
    """
    Three-table JOIN: orders + users + products.
    Medium cost — join on non-indexed foreign keys.
    QuerySentinel should score this as MEDIUM COST.
    """
    sql = """
        SELECT
            o.id        AS order_id,
            u.name      AS customer_name,
            u.country,
            p.name      AS product_name,
            p.category,
            o.quantity,
            o.total,
            o.status,
            o.ordered_at
        FROM orders o
        JOIN users    u ON u.id = o.user_id
        JOIN products p ON p.id = o.product_id
        WHERE o.status != 'cancelled'
        ORDER BY o.ordered_at DESC
        LIMIT 50
    """
    results = run_query(sql)
    return jsonify({
        "route": "/orders",
        "query_type": "join_query",
        "count": len(results),
        "data": results
    })


# ─── Route 3: Aggregation ──────────────────────────────────────────────────────
# Query type : GROUP BY aggregation with HAVING
# Expected cost : MEDIUM-HIGH (scans entire orders table)
# What QuerySentinel learns: aggregations across many rows are expensive

@app.route("/reports")
def get_reports():
    """
    GROUP BY aggregation: revenue per product category.
    Scans all 1000 orders rows, groups, aggregates.
    QuerySentinel should score this as MEDIUM-HIGH COST.
    """
    sql = """
        SELECT
            p.category,
            COUNT(o.id)             AS total_orders,
            SUM(o.total)            AS total_revenue,
            AVG(o.total)            AS avg_order_value,
            MAX(o.total)            AS max_order,
            COUNT(DISTINCT o.user_id) AS unique_customers
        FROM orders o
        JOIN products p ON p.id = o.product_id
        GROUP BY p.category
        HAVING COUNT(o.id) > 5
        ORDER BY total_revenue DESC
    """
    results = run_query(sql)
    return jsonify({
        "route": "/reports",
        "query_type": "aggregation",
        "count": len(results),
        "data": results
    })


# ─── Route 4: Full table scan ─────────────────────────────────────────────────
# Query type : LIKE pattern on unindexed text column
# Expected cost : HIGH (sequential scan, no index possible for leading wildcard)
# What QuerySentinel learns: LIKE '%...' kills performance — flag this

@app.route("/search")
def search_products():
    """
    Full table scan: LIKE search with leading wildcard.
    PostgreSQL CANNOT use an index for '%term' pattern.
    Forces a sequential scan of the entire products table.
    QuerySentinel should score this as HIGH COST and flag it.
    """
    search_term = "product"   # hardcoded for demo

    sql = """
        SELECT
            p.id,
            p.name,
            p.category,
            p.price,
            p.stock,
            AVG(r.rating) AS avg_rating,
            COUNT(r.id)   AS review_count
        FROM products p
        LEFT JOIN reviews r ON r.product_id = p.id
        WHERE LOWER(p.name) LIKE %s
           OR LOWER(p.category) LIKE %s
        GROUP BY p.id, p.name, p.category, p.price, p.stock
        ORDER BY avg_rating DESC NULLS LAST
    """
    pattern = f"%{search_term}%"
    results = run_query(sql, (pattern, pattern))
    return jsonify({
        "route": "/search",
        "query_type": "full_table_scan",
        "warning": "LIKE with leading wildcard = sequential scan = expensive",
        "count": len(results),
        "data": results
    })


# ─── Route 5: Nested subquery ─────────────────────────────────────────────────
# Query type : Correlated subquery inside SELECT
# Expected cost : VERY HIGH (subquery runs once per row)
# What QuerySentinel learns: correlated subqueries are the worst pattern

@app.route("/summary")
def get_summary():
    """
    Correlated subquery: for each user, count their orders and reviews.
    The subqueries run independently for EVERY user row.
    With 500 users this means 1000+ extra queries internally.
    QuerySentinel should score this VERY HIGH and suggest rewrite to JOIN.
    """
    sql = """
        SELECT
            u.id,
            u.name,
            u.country,
            (
                SELECT COUNT(*)
                FROM orders o
                WHERE o.user_id = u.id
            ) AS total_orders,
            (
                SELECT COALESCE(SUM(o2.total), 0)
                FROM orders o2
                WHERE o2.user_id = u.id
                  AND o2.status = 'delivered'
            ) AS total_spent,
            (
                SELECT COUNT(*)
                FROM reviews r
                WHERE r.user_id = u.id
            ) AS total_reviews,
            (
                SELECT AVG(r2.rating)
                FROM reviews r2
                WHERE r2.user_id = u.id
            ) AS avg_rating_given
        FROM users u
        ORDER BY total_orders DESC
        LIMIT 30
    """
    results = run_query(sql)
    return jsonify({
        "route": "/summary",
        "query_type": "correlated_subquery",
        "warning": "Correlated subquery = N+1 problem = very expensive",
        "count": len(results),
        "data": results
    })


# ─── Health check ─────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Quick check that DB connection works."""
    try:
        run_query("SELECT 1 AS ok")
        return jsonify({"status": "ok", "db": "connected"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  QuerySentinel Test App")
    print("="*50)
    print("  Routes:")
    print("  GET /health   — DB connection check")
    print("  GET /users    — simple SELECT       (low cost)")
    print("  GET /orders   — JOIN query          (medium cost)")
    print("  GET /reports  — GROUP BY aggregation (medium-high)")
    print("  GET /search   — full table scan     (high cost)")
    print("  GET /summary  — correlated subquery (very high)")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)
