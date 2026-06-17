"""
QuerySentinel — Proxy Demo v2 (Week 2)
========================================
Demonstrates ML prediction BEFORE query execution.
Shows DANGER queries being blocked automatically.

Run: python proxy/main_v2.py

Expected output:
  [ML PREDICT] LOW    (confidence: 85%) | action: ALLOW
  [ML PREDICT] MEDIUM (confidence: 72%) | action: ALLOW
  [ML PREDICT] HIGH   (confidence: 68%) | action: FLAG
  [ML PREDICT] DANGER (confidence: 91%) | action: BLOCK_AND_REWRITE
  [BLOCKED] DANGER query stopped by QuerySentinel
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from proxy.interceptor import InterceptedConnection, DangerousQueryError
from storage.writer import get_expensive_queries

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

QUERIES = [
    {
        "label": "Simple SELECT [expected: LOW -> ALLOW]",
        "sql": "SELECT id, name, email FROM users ORDER BY id LIMIT 20",
    },
    {
        "label": "JOIN query [expected: MEDIUM -> ALLOW]",
        "sql": """
            SELECT o.id, u.name, p.name AS product, o.total
            FROM orders o
            JOIN users    u ON u.id = o.user_id
            JOIN products p ON p.id = o.product_id
            WHERE o.status != 'cancelled'
            LIMIT 50
        """,
    },
    {
        "label": "GROUP BY aggregation [expected: MEDIUM/HIGH -> FLAG]",
        "sql": """
            SELECT p.category, COUNT(o.id) AS orders, SUM(o.total) AS revenue
            FROM orders o
            JOIN products p ON p.id = o.product_id
            GROUP BY p.category
            ORDER BY revenue DESC
        """,
    },
    {
        "label": "Full table scan LIKE [expected: HIGH -> FLAG]",
        "sql": "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'",
    },
    {
        "label": "Correlated subquery [expected: DANGER -> BLOCK]",
        "sql": """
            SELECT u.id, u.name,
                (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
                (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews,
                (SELECT AVG(r2.rating) FROM reviews r2 WHERE r2.user_id = u.id) AS avg_rating
            FROM users u LIMIT 30
        """,
    },
]


def run_demo():
    print("\n" + "="*65)
    print("  QuerySentinel v2 -- ML Prediction BEFORE Execution")
    print("  Week 2 Demo")
    print("="*65)

    real_conn = psycopg2.connect(**DB_CONFIG)
    log_conn  = psycopg2.connect(**DB_CONFIG)

    # block_danger=True means DANGER queries are stopped before they run
    intercepted = InterceptedConnection(
        real_conn,
        log_conn=log_conn,
        block_danger=True,
    )

    allowed  = 0
    flagged  = 0
    blocked  = 0

    with intercepted:
        cur = intercepted.cursor()

        for q in QUERIES:
            print(f"\n\n>>> TEST: {q['label']}")
            try:
                cur.execute(q["sql"])
                rows = cur.fetchall()
                print(f"    Returned {len(rows)} rows")
                allowed += 1
            except DangerousQueryError as e:
                print(f"    [BLOCKED] Query was stopped: {str(e)[:80]}")
                blocked += 1
            except Exception as e:
                print(f"    [ERROR] {e}")

    # ── Final summary ─────────────────────────────────────────
    print("\n\n" + "="*65)
    print("  WEEK 2 DEMO COMPLETE")
    print("="*65)
    print(f"  Queries allowed  : {allowed}")
    print(f"  Queries flagged  : {intercepted.flagged_count}")
    print(f"  Queries BLOCKED  : {intercepted.blocked_count}")
    print(f"\n  This is QuerySentinel's core value:")
    print(f"  Expensive queries are stopped BEFORE they hit the DB.")
    print(f"  Week 3: LLM agent rewrites blocked queries automatically.")

    # Show prediction accuracy from logs
    log_conn2 = psycopg2.connect(**DB_CONFIG)
    try:
        with log_conn2.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*)                                     AS total,
                    SUM(CASE WHEN node_type = 'BLOCKED' THEN 1 ELSE 0 END) AS blocked
                FROM query_logs
            """)
            row = cur.fetchone()
            print(f"\n  Total queries in log : {row[0]}")
            print(f"  Blocked queries      : {row[1]}")
    except Exception as e:
        print(f"  [LOG CHECK] {e}")
    finally:
        log_conn2.close()

    print("\n  Open MLflow: mlflow ui --port 5001")
    print("  Open pgAdmin: http://localhost:8080")
    print("="*65 + "\n")


if __name__ == "__main__":
    run_demo()
