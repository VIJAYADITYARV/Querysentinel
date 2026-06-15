"""
QuerySentinel — Proxy Entry Point
====================================
Wires the interceptor into the test app
and starts logging all queries.

Run this BEFORE starting the Flask test app.

Usage:
    python proxy/main.py
"""

import sys
import os

# Make sure imports work from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from proxy.interceptor import InterceptedConnection
from storage.writer import get_expensive_queries

# ─── Config ───────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


# ─── Demo: run all 5 query types through the interceptor ─────────────────────

def run_demo():
    """
    Demonstrates QuerySentinel intercepting queries directly.
    In production, the Flask app would use InterceptedConnection.
    """
    print("\n" + "="*60)
    print("  QuerySentinel — Live Query Interception Demo")
    print("="*60)

    # Connect through the interceptor
    real_conn  = psycopg2.connect(**DB_CONFIG)
    log_conn   = psycopg2.connect(**DB_CONFIG)
    intercepted = InterceptedConnection(real_conn, log_pool=log_conn)

    queries = [
        # (label, sql)
        (
            "Simple SELECT — expected LOW cost",
            "SELECT id, name, email FROM users ORDER BY id LIMIT 20"
        ),
        (
            "JOIN query — expected MEDIUM cost",
            """
            SELECT o.id, u.name, p.name AS product, o.total, o.status
            FROM orders o
            JOIN users    u ON u.id = o.user_id
            JOIN products p ON p.id = o.product_id
            WHERE o.status != 'cancelled'
            LIMIT 50
            """
        ),
        (
            "GROUP BY aggregation — expected MEDIUM-HIGH cost",
            """
            SELECT p.category, COUNT(o.id) AS orders, SUM(o.total) AS revenue
            FROM orders o
            JOIN products p ON p.id = o.product_id
            GROUP BY p.category
            ORDER BY revenue DESC
            """
        ),
        (
            "Full table scan (LIKE) — expected HIGH cost",
            "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'"
        ),
        (
            "Correlated subquery — expected DANGER cost",
            """
            SELECT u.id, u.name,
                (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders,
                (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
            FROM users u
            LIMIT 30
            """
        ),
    ]

    with intercepted:
        cur = intercepted.cursor()
        for label, sql in queries:
            print(f"\n\n>>> TEST: {label}")
            cur.execute(sql)
            rows = cur.fetchall()
            print(f"    Returned {len(rows)} rows")

    # ── Summary ───────────────────────────────────────────────
    print("\n\n" + "="*60)
    print("  DEMO COMPLETE")
    print(f"  Total queries intercepted: {intercepted.intercepted_count}")
    print("="*60)

    # Show most expensive queries from TimescaleDB
    log_conn = psycopg2.connect(**DB_CONFIG)
    expensive = get_expensive_queries(log_conn, limit=5)
    log_conn.close()

    if expensive:
        print("\n  TOP 5 MOST EXPENSIVE QUERIES LOGGED:")
        print("  " + "-"*56)
        for i, q in enumerate(expensive, 1):
            print(f"  {i}. Cost: {q['total_cost']:.1f} | "
                  f"Node: {q['node_type']} | "
                  f"Time: {q['exec_ms']:.1f}ms")
            print(f"     SQL: {q['query_preview'][:70]}...")
        print("  " + "-"*56)
        print("\n  These are your Week 2 ML training targets !")

    print("\n  Open pgAdmin at http://localhost:8081")
    print("  Email: admin@querysentinel.com  |  Password: admin")
    print("  Run: SELECT * FROM query_logs ORDER BY total_cost DESC;")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_demo()
