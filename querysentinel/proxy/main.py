"""
QuerySentinel — Proxy Main (Week 1 Final)
==========================================
Runs all 5 query types through the interceptor.
Port 5433 (your Windows-fixed port).

Run: python proxy/main.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from proxy.interceptor import InterceptedConnection
from storage.writer import get_expensive_queries

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

QUERIES = [
    (
        "Simple SELECT [LOW cost]",
        "SELECT id, name, email FROM users ORDER BY id LIMIT 20"
    ),
    (
        "JOIN query [MEDIUM cost]",
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
        "GROUP BY aggregation [MEDIUM-HIGH cost]",
        """
        SELECT p.category, COUNT(o.id) AS orders, SUM(o.total) AS revenue
        FROM orders o
        JOIN products p ON p.id = o.product_id
        GROUP BY p.category
        ORDER BY revenue DESC
        """
    ),
    (
        "Full table scan LIKE [HIGH cost]",
        "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'"
    ),
    (
        "Correlated subquery [DANGER cost]",
        """
        SELECT u.id, u.name,
            (SELECT COUNT(*) FROM orders o   WHERE o.user_id = u.id) AS orders,
            (SELECT COUNT(*) FROM reviews r  WHERE r.user_id = u.id) AS reviews
        FROM users u
        LIMIT 30
        """
    ),
]


def run_demo():
    print("\n" + "="*62)
    print("  QuerySentinel -- Proxy Demo (Week 1 Final)")
    print("  Port: 5433")
    print("="*62)

    real_conn   = psycopg2.connect(**DB_CONFIG)
    log_conn    = psycopg2.connect(**DB_CONFIG)  # separate connection for writes
    intercepted = InterceptedConnection(real_conn, log_conn=log_conn)

    with intercepted:
        cur = intercepted.cursor()
        for label, sql in QUERIES:
            print(f"\n>>> {label}")
            cur.execute(sql)
            rows = cur.fetchall()
            print(f"    Returned {len(rows)} rows")

    print("\n\n" + "="*62)
    print(f"  COMPLETE -- {intercepted.intercepted_count} queries intercepted")
    print("="*62)

    # Show top expensive queries from DB
    log_conn2 = psycopg2.connect(**DB_CONFIG)
    expensive = get_expensive_queries(log_conn2, limit=5)
    log_conn2.close()

    if expensive:
        print("\n  TOP 5 MOST EXPENSIVE QUERIES:")
        print("  " + "-"*58)
        for i, q in enumerate(expensive, 1):
            cost = float(q["total_cost"] or 0)
            cat  = q.get("cost_category", "?")
            node = q.get("node_type", "?")
            ms   = float(q.get("exec_ms") or 0)
            prev = str(q.get("query_preview", ""))[:65]
            print(f"  {i}. [{cat}] Cost={cost:.1f} | Node={node} | {ms:.1f}ms")
            print(f"     {prev}...")

    print("\n  pgAdmin  : http://localhost:8080")
    print("  Login    : admin@querysentinel.com / admin")
    print("  Verify   : SELECT * FROM query_logs ORDER BY total_cost DESC;")
    print("="*62 + "\n")


if __name__ == "__main__":
    run_demo()
