"""
QuerySentinel — Week 1 Completion Analysis (Day 5)
====================================================
Run this at the end of Week 1 to:
  1. Fire 50 queries through all 5 Flask routes
  2. Show category breakdown from TimescaleDB
  3. Show top 5 most expensive queries
  4. Export ML training data to CSV (used in Week 2)
  5. Print your interview-ready summary

Usage:
    # Make sure flask app is running: python testapp/app.py
    python analysis.py
"""

import os
import sys
import csv
import time
import requests
import psycopg2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage.writer import (
    get_expensive_queries,
    get_category_breakdown,
    get_ml_training_export,
)

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "http://localhost:5000"
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

ROUTES = [
    ("/users",   "simple SELECT    [LOW]"),
    ("/orders",  "JOIN query       [MEDIUM]"),
    ("/reports", "aggregation      [MEDIUM-HIGH]"),
    ("/search",  "full table scan  [HIGH]"),
    ("/summary", "correlated sub   [DANGER]"),
]


def step1_fire_traffic(rounds: int = 10):
    """Fire 50 queries (10 rounds x 5 routes) through Flask app."""
    print("\n" + "="*60)
    print("  STEP 1 — Generating 50 queries through all 5 routes")
    print("="*60)

    success = 0
    failed  = 0

    for r in range(1, rounds + 1):
        print(f"\n  Round {r}/{rounds}:")
        for route, label in ROUTES:
            try:
                resp = requests.get(f"{BASE_URL}{route}", timeout=15)
                icon = "[OK]" if resp.status_code == 200 else "[FAIL]"
                print(f"    {icon}  {route:<12}  {label}")
                if resp.status_code == 200:
                    success += 1
                else:
                    failed += 1
            except requests.exceptions.ConnectionError:
                print(f"    [ERR]  {route:<12}  Cannot connect — is app.py running?")
                failed += 1
            time.sleep(0.15)

    print(f"\n  Total: {success} success, {failed} failed")
    return success


def step2_category_breakdown(conn):
    """Show query cost distribution in TimescaleDB."""
    print("\n" + "="*60)
    print("  STEP 2 — Cost Category Breakdown in TimescaleDB")
    print("="*60)

    rows = get_category_breakdown(conn)
    if not rows:
        print("  No data yet. Make sure queries are being logged.")
        return

    print(f"\n  {'Category':<12} {'Count':>6} {'Avg Cost':>10} "
          f"{'Avg ms':>8} {'SeqScans':>9} {'NestLoops':>10}")
    print("  " + "-"*58)

    for r in rows:
        print(
            f"  {str(r['cost_category']):<12} "
            f"{r['total_queries']:>6} "
            f"{float(r['avg_cost'] or 0):>10.2f} "
            f"{float(r['avg_exec_ms'] or 0):>8.2f} "
            f"{r['seq_scans']:>9} "
            f"{r['nested_loops']:>10}"
        )


def step3_expensive_queries(conn):
    """Show the top 5 most expensive queries."""
    print("\n" + "="*60)
    print("  STEP 3 — Top 5 Most Expensive Queries")
    print("  (These are your Week 2 ML training targets)")
    print("="*60)

    expensive = get_expensive_queries(conn, limit=5)
    if not expensive:
        print("  No data yet.")
        return

    for i, q in enumerate(expensive, 1):
        cost     = float(q["total_cost"] or 0)
        danger   = float(q["danger_score"] or 0)
        category = q["cost_category"] or "?"
        node     = q["node_type"] or "?"
        ms       = float(q["exec_ms"] or 0)
        seq      = "YES" if q["has_seq_scan"] else "no"
        nested   = "YES" if q["has_nested_loop"] else "no"
        preview  = str(q["query_preview"] or "")[:70]

        print(f"\n  [{i}] {category} — Cost: {cost:.1f} | "
              f"Danger: {danger:.2f} | Time: {ms:.1f}ms")
        print(f"      Node: {node} | SeqScan: {seq} | NestedLoop: {nested}")
        print(f"      SQL:  {preview}...")


def step4_export_csv(conn):
    """Export ML training data to CSV for Week 2."""
    print("\n" + "="*60)
    print("  STEP 4 — Exporting ML Training Data to CSV")
    print("="*60)

    rows = get_ml_training_export(conn)
    if not rows:
        print("  No data to export.")
        return

    csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "ml_training_data.csv"
    )

    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Exported {len(rows)} rows to: ml_training_data.csv")
    print(f"  Columns: {', '.join(fieldnames)}")
    print(f"\n  This CSV is your Week 2 starting point.")
    print(f"  Load it with: pd.read_csv('ml_training_data.csv')")

    # Show label distribution
    from collections import Counter
    labels = Counter(r["cost_category"] for r in rows)
    print(f"\n  Label distribution:")
    for label, count in sorted(labels.items()):
        bar = "#" * count
        print(f"    {label:<10} {count:>4}  {bar}")


def step5_interview_summary(conn):
    """Print your interview-ready summary card."""
    print("\n" + "="*60)
    print("  STEP 5 — Week 1 Complete: Interview Summary")
    print("="*60)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM query_logs")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT MAX(total_cost), MIN(total_cost), AVG(exec_ms)::NUMERIC(10,2)
            FROM query_logs WHERE total_cost IS NOT NULL
        """)
        max_cost, min_cost, avg_ms = cur.fetchone()

    print(f"""
  What you built this week:
  -------------------------
  - Transparent SQL proxy intercepting ALL database queries
  - EXPLAIN ANALYZE running automatically on every SELECT
  - TimescaleDB hypertable storing {total} query logs
  - Danger scoring (0-1) computed per query
  - 4-class cost labelling (LOW/MEDIUM/HIGH/DANGER)
  - ML training data exported to CSV

  Key metrics discovered:
  -----------------------
  - Total queries logged   : {total}
  - Most expensive query   : {float(max_cost or 0):.1f} cost units
  - Cheapest query         : {float(min_cost or 0):.1f} cost units
  - Average execution time : {float(avg_ms or 0):.2f}ms

  What you say in an interview:
  -----------------------------
  "I built a transparent PostgreSQL proxy in Python that
   intercepts every SQL query, runs EXPLAIN ANALYZE
   automatically, extracts 15 ML features from the execution
   plan, and stores them in a TimescaleDB hypertable.
   I collected {total} labelled query examples this week.
   In Week 2 I train a GNN on those execution plans to
   predict query cost before the query runs."

  Week 2 starts now:
  ------------------
  - Load ml_training_data.csv
  - Train ML cost predictor (GNN on query plan features)
  - Integrate predictor into the proxy
  - High-cost queries flagged BEFORE they execute
""")
    print("="*60 + "\n")


def main():
    print("\n" + "="*60)
    print("  QuerySentinel — Week 1 Completion Analysis")
    print("="*60)

    # Connect to TimescaleDB
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        print("  Database connected.")
    except Exception as e:
        print(f"  [ERROR] Cannot connect to database: {e}")
        print("  Make sure docker-compose is running.")
        return

    step1_fire_traffic(rounds=10)
    step2_category_breakdown(conn)
    step3_expensive_queries(conn)
    step4_export_csv(conn)
    step5_interview_summary(conn)

    conn.close()


if __name__ == "__main__":
    main()
