"""
QuerySentinel — Proxy Demo v3 (Week 3)
=========================================
The full autonomous loop in action:
  DANGER query -> LLM agent rewrites -> validates -> executes fixed version

Run: python proxy/main_v3.py

Requires: GEMINI_API_KEY set in environment.
    Windows PowerShell:  $env:GEMINI_API_KEY = "AIza..."
    Or use a .env file with python-dotenv (see bottom of file)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file if present (optional convenience)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2
from proxy.interceptor import InterceptedConnection, EscalatedQueryError
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
        "label": "Simple SELECT [expected: LOW -> ALLOW, no agent needed]",
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
        "label": "Correlated subquery [expected: DANGER -> AGENT REWRITES]",
        "sql": """
            SELECT u.id, u.name,
                (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
                (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
            FROM users u LIMIT 30
        """,
    },
]


def run_demo():
    if not os.getenv("GEMINI_API_KEY"):
        print("\n[ERROR] GEMINI_API_KEY not set.")
        print("  Set it with: $env:GEMINI_API_KEY = 'AIza...'  (PowerShell)")
        print("  Or create a .env file with: GEMINI_API_KEY=AIza...")
        return

    print("\n" + "="*65)
    print("  QuerySentinel v3 -- Autonomous Detect + Rewrite + Execute")
    print("  Week 3 Demo")
    print("="*65)

    real_conn = psycopg2.connect(**DB_CONFIG)
    log_conn  = psycopg2.connect(**DB_CONFIG)

    intercepted = InterceptedConnection(
        real_conn,
        log_conn=log_conn,
        auto_rewrite=True,    # Week 3: rewrite instead of blocking
    )

    with intercepted:
        cur = intercepted.cursor()

        for q in QUERIES:
            print(f"\n\n>>> TEST: {q['label']}")
            try:
                cur.execute(q["sql"])
                rows = cur.fetchall()
                print(f"    Returned {len(rows)} rows")
            except EscalatedQueryError as e:
                print(f"    [ESCALATED] {str(e)[:100]}")
            except Exception as e:
                print(f"    [ERROR] {e}")

    print("\n\n" + "="*65)
    print("  WEEK 3 DEMO COMPLETE")
    print("="*65)
    print(f"\n  This is the full QuerySentinel value proposition:")
    print(f"  - Detects dangerous queries before they hit the DB")
    print(f"  - An LLM agent automatically rewrites them")
    print(f"  - Every rewrite is VALIDATED with EXPLAIN before trusting it")
    print(f"  - Only safe, faster queries actually execute")
    print(f"\n  Open pgAdmin: http://localhost:8080")
    print(f"  Check query_logs for was_rewritten=true rows")
    print("="*65 + "\n")


if __name__ == "__main__":
    run_demo()
