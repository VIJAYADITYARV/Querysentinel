"""
QuerySentinel — Test App (Day 2 Update)
=========================================
NOW uses InterceptedConnection instead of plain psycopg2.
Every Flask route query flows through the proxy automatically.

Run this AFTER proxy/main.py is running.
    python testapp/app.py

Then hit routes in browser or with curl:
    curl http://localhost:5000/users
    curl http://localhost:5000/orders
    curl http://localhost:5000/reports
    curl http://localhost:5000/search
    curl http://localhost:5000/summary
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify
from proxy.interceptor import InterceptedConnection

app = Flask(__name__)

# ── Config (port 5433 — your fixed docker port) ───────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


def get_intercepted_connection():
    """
    Returns an InterceptedConnection instead of plain psycopg2.
    This is the ONE change from Day 1 — everything else stays identical.
    Every query this app runs now goes through QuerySentinel.
    """
    real_conn = psycopg2.connect(**DB_CONFIG)
    return InterceptedConnection(real_conn)


def run_query(sql, params=None):
    """Execute SQL through the intercepted connection."""
    conn = get_intercepted_connection()
    try:
        # Use RealDictCursor for JSON-friendly results
        with conn._conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as real_cur:
            # Manually trigger interception for logging
            intercepted_cur = conn.cursor()
            intercepted_cur.execute(sql, params)

            # Fetch results from the real cursor
            real_cur.execute(sql, params)
            results = real_cur.fetchall()
            return [dict(row) for row in results]
    finally:
        conn.close()


# ── Route 1: Simple SELECT ────────────────────────────────────────────────────
@app.route("/users")
def get_users():
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
        "expected_cost": "LOW",
        "count": len(results),
        "data": results
    })


# ── Route 2: JOIN query ───────────────────────────────────────────────────────
@app.route("/orders")
def get_orders():
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
        "expected_cost": "MEDIUM",
        "count": len(results),
        "data": results
    })


# ── Route 3: Aggregation ──────────────────────────────────────────────────────
@app.route("/reports")
def get_reports():
    sql = """
        SELECT
            p.category,
            COUNT(o.id)               AS total_orders,
            SUM(o.total)              AS total_revenue,
            AVG(o.total)              AS avg_order_value,
            MAX(o.total)              AS max_order,
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
        "expected_cost": "MEDIUM-HIGH",
        "count": len(results),
        "data": results
    })


# ── Route 4: Full table scan ──────────────────────────────────────────────────
@app.route("/search")
def search_products():
    search_term = "product"
    sql = """
        SELECT
            p.id,
            p.name,
            p.category,
            p.price,
            p.stock,
            AVG(r.rating)  AS avg_rating,
            COUNT(r.id)    AS review_count
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
        "expected_cost": "HIGH",
        "warning": "LIKE with leading wildcard forces sequential scan",
        "count": len(results),
        "data": results
    })


# ── Route 5: Correlated subquery ──────────────────────────────────────────────
@app.route("/summary")
def get_summary():
    sql = """
        SELECT
            u.id,
            u.name,
            u.country,
            (SELECT COUNT(*)
             FROM orders o WHERE o.user_id = u.id)             AS total_orders,
            (SELECT COALESCE(SUM(o2.total), 0)
             FROM orders o2
             WHERE o2.user_id = u.id
               AND o2.status = 'delivered')                    AS total_spent,
            (SELECT COUNT(*)
             FROM reviews r WHERE r.user_id = u.id)            AS total_reviews,
            (SELECT AVG(r2.rating)
             FROM reviews r2 WHERE r2.user_id = u.id)          AS avg_rating_given
        FROM users u
        ORDER BY total_orders DESC
        LIMIT 30
    """
    results = run_query(sql)
    return jsonify({
        "route": "/summary",
        "query_type": "correlated_subquery",
        "expected_cost": "DANGER",
        "warning": "Correlated subquery = N+1 problem. QuerySentinel will rewrite this in Week 3.",
        "count": len(results),
        "data": results
    })


# ── Health check ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM query_logs")
            log_count = cur.fetchone()[0]
        conn.close()
        return jsonify({
            "status": "ok",
            "db": "connected",
            "queries_logged_so_far": log_count
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


if __name__ == "__main__":
    print("\n" + "="*55)
    print("  QuerySentinel Test App — DAY 2")
    print("  All routes now use InterceptedConnection")
    print("  Every query flows through QuerySentinel proxy")
    print("="*55)
    print("  GET /health   -- DB check + log count")
    print("  GET /users    -- simple SELECT    [LOW cost]")
    print("  GET /orders   -- JOIN query       [MEDIUM cost]")
    print("  GET /reports  -- GROUP BY         [MEDIUM-HIGH]")
    print("  GET /search   -- full table scan  [HIGH cost]")
    print("  GET /summary  -- correlated sub   [DANGER]")
    print("="*55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)
