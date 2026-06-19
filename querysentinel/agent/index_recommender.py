"""
QuerySentinel — Index Recommender (Week 3)
=============================================
The "self-healing" layer. Analyses query_logs over time
and recommends indexes that would prevent future DANGER
queries — not just reacting, but learning patterns.

This is what makes QuerySentinel "autonomous" rather than
just a reactive blocker — it improves the database itself.

Run standalone:
    python agent/index_recommender.py

Or call analyse_and_recommend() periodically (e.g. daily cron).
"""

import os
import sys
import json
import re
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


def analyse_and_recommend() -> list:
    """
    Look at recent query_logs, find recurring sequential scans
    on the same table+column patterns, and recommend indexes.

    Returns a list of recommendation dicts.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    recommendations = []

    try:
        # Find queries with seq scans that ran multiple times
        sql = """
            SELECT raw_sql, COUNT(*) as occurrences, AVG(total_cost) as avg_cost
            FROM query_logs
            WHERE has_seq_scan = TRUE
              AND total_cost > 50
            GROUP BY raw_sql
            HAVING COUNT(*) >= 2
            ORDER BY avg_cost DESC
            LIMIT 20
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        for raw_sql, occurrences, avg_cost in rows:
            rec = _extract_index_candidate(raw_sql)
            if rec:
                rec["occurrences"] = occurrences
                rec["avg_cost"]    = float(avg_cost)
                recommendations.append(rec)

        # Deduplicate by table+column
        seen = set()
        unique_recs = []
        for rec in recommendations:
            key = (rec["table"], rec["column"])
            if key not in seen:
                seen.add(key)
                unique_recs.append(rec)

        return unique_recs

    finally:
        conn.close()


def _extract_index_candidate(sql: str) -> dict:
    """
    Parse a query to find WHERE/JOIN columns that are likely
    candidates for indexing — simple regex-based heuristic.

    Looks for patterns like:
        WHERE table.column = ...
        JOIN table ON table.column = other.column
    """
    sql_clean = sql.upper()

    # Look for WHERE clause column references
    where_match = re.search(r'WHERE\s+(\w+)\.(\w+)', sql, re.IGNORECASE)
    if where_match:
        return {
            "table":  where_match.group(1).lower(),
            "column": where_match.group(2).lower(),
            "reason": "Frequently filtered in WHERE clause with sequential scan",
        }

    # Look for JOIN ON column references
    join_match = re.search(r'JOIN\s+(\w+)\s+\w*\s*ON\s+\w+\.(\w+)\s*=\s*(\w+)\.(\w+)',
                            sql, re.IGNORECASE)
    if join_match:
        return {
            "table":  join_match.group(3).lower(),
            "column": join_match.group(4).lower(),
            "reason": "Used as JOIN key without index",
        }

    return None


def generate_index_sql(recommendation: dict) -> str:
    """Generate the actual CREATE INDEX statement for a recommendation."""
    table  = recommendation["table"]
    column = recommendation["column"]
    idx_name = f"idx_{table}_{column}"
    return f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column});"


def print_recommendations(recommendations: list):
    """Pretty-print recommendations for the dashboard / CLI."""
    if not recommendations:
        print("\n  No index recommendations at this time.")
        print("  (Either no seq scans detected, or not enough repeat occurrences yet)")
        return

    print(f"\n  Found {len(recommendations)} index recommendation(s):\n")
    for i, rec in enumerate(recommendations, 1):
        sql = generate_index_sql(rec)
        print(f"  [{i}] Table: {rec['table']}  Column: {rec['column']}")
        print(f"      Reason: {rec['reason']}")
        print(f"      Seen {rec['occurrences']} times, avg cost {rec['avg_cost']:.1f}")
        print(f"      Suggested SQL:")
        print(f"      {sql}")
        print()


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  QuerySentinel — Self-Healing Index Recommender")
    print("="*60)

    recs = analyse_and_recommend()
    print_recommendations(recs)

    print("="*60)
    print("  This is the 'self-healing' layer Oracle interviewers love:")
    print("  QuerySentinel doesn't just flag problems — it learns")
    print("  schema-level fixes from accumulated query history.")
    print("="*60 + "\n")
