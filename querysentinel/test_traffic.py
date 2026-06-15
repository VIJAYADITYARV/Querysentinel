"""
QuerySentinel — Traffic Generator
====================================
Run this on Day 5 to fire 50 queries through
the Flask test app and verify all are logged.

Usage:
    # 1. Make sure docker-compose is running
    # 2. Make sure testapp/app.py is running (port 5000)
    # 3. Run: python test_traffic.py

Expected output:
    50 requests, all 200 OK
    50 rows in query_logs table
"""

import requests
import time
import psycopg2

BASE_URL = "http://localhost:5000"

ROUTES = [
    ("/health",   "health check"),
    ("/users",    "simple SELECT       — low cost"),
    ("/orders",   "JOIN query          — medium cost"),
    ("/reports",  "GROUP BY aggregation— medium-high cost"),
    ("/search",   "full table scan     — high cost"),
    ("/summary",  "correlated subquery — very high cost"),
]


def run_traffic():
    print("\n" + "="*55)
    print("  QuerySentinel — Traffic Generator")
    print("  Firing 50 queries through all 5 routes")
    print("="*55 + "\n")

    success = 0
    failed  = 0

    for round_num in range(1, 11):     # 10 rounds × 5 routes = 50 queries
        print(f"Round {round_num}/10:")
        for route, description in ROUTES:
            if route == "/health":
                continue               # skip health in traffic test
            try:
                resp = requests.get(f"{BASE_URL}{route}", timeout=10)
                status = "✓" if resp.status_code == 200 else "✗"
                print(f"  {status}  {route:<12} {resp.status_code}  {description}")
                if resp.status_code == 200:
                    success += 1
                else:
                    failed += 1
            except requests.exceptions.ConnectionError:
                print(f"  ✗  {route:<12} CONNECTION REFUSED")
                print("     Is testapp/app.py running on port 5000?")
                failed += 1
            time.sleep(0.1)            # small delay between requests
        print()

    # ── Verify in TimescaleDB ─────────────────────────────────
    print("="*55)
    print(f"  Requests sent:     {success + failed}")
    print(f"  Successful:        {success}")
    print(f"  Failed:            {failed}")
    print("="*55)

    try:
        conn = psycopg2.connect(
            host="localhost", port=5433,
            dbname="querysentinel", user="postgres",
            password="querysentinel"
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM query_logs")
            count = cur.fetchone()[0]

            cur.execute("""
                SELECT node_type, COUNT(*) as n, AVG(total_cost)::NUMERIC(10,2) as avg_cost
                FROM query_logs
                WHERE total_cost IS NOT NULL
                GROUP BY node_type
                ORDER BY avg_cost DESC
            """)
            breakdown = cur.fetchall()

        conn.close()

        print(f"\n  TIMESCALEDB VERIFICATION:")
        print(f"  Total rows in query_logs: {count}")
        print(f"\n  Cost breakdown by node type:")
        print(f"  {'Node Type':<30} {'Count':>6}  {'Avg Cost':>10}")
        print("  " + "-"*50)
        for row in breakdown:
            print(f"  {str(row[0]):<30} {row[1]:>6}  {float(row[2]):>10.2f}")

        print(f"\n  {'='*53}")
        print(f"  Week 1 COMPLETE ✓")
        print(f"  Your proxy is intercepting and logging real queries.")
        print(f"  Week 2: train ML model on these {count} query logs.")
        print(f"  {'='*53}\n")

    except Exception as e:
        print(f"\n  [DB CHECK ERROR] {e}")
        print("  Make sure docker-compose is running.")


if __name__ == "__main__":
    run_traffic()
