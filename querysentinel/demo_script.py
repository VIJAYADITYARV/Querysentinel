"""
QuerySentinel — Full Demo Script (Week 4)
=============================================
Run this ONE script in your interview demo. It walks through
the entire system end to end with clear narration printed
between each step, so you can talk over it naturally.

Run: python demo_script.py

Narrative arc:
  1. A cheap query passes through cleanly (baseline)
  2. A medium query gets flagged but allowed
  3. A genuinely dangerous correlated subquery gets BLOCKED
  4. The LLM agent diagnoses, rewrites, and validates a fix
  5. The fixed query executes — show the cost improvement
  6. The self-healing layer suggests an index based on patterns
  7. Show the live dashboard is now populated (open browser)

This script assumes Weeks 1-4 files are all in place and
the predictor/agent bugfixes from this conversation are applied.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2
from proxy.interceptor import InterceptedConnection, EscalatedQueryError
from agent.index_recommender import analyse_and_recommend, print_recommendations

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


def narrate(text, pause=1.5):
    """Print a narration line and pause — gives you time to talk over it."""
    print(f"\n  >>> {text}")
    time.sleep(pause)


def section(title):
    print("\n\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def run_full_demo():
    if not os.getenv("GEMINI_API_KEY"):
        print("\n[ERROR] GEMINI_API_KEY not set — required for the rewrite agent demo.")
        return

    section("QUERYSENTINEL — FULL SYSTEM DEMO")
    narrate("Autonomous Database Query Intelligence Platform")
    narrate("Built over 4 weeks: interception -> ML prediction -> LLM agent -> self-healing")

    real_conn = psycopg2.connect(**DB_CONFIG)
    log_conn  = psycopg2.connect(**DB_CONFIG)
    intercepted = InterceptedConnection(real_conn, log_conn=log_conn, auto_rewrite=True)

    # ── Step 1: cheap query, clean pass ────────────────────────
    section("STEP 1 — A normal query passes through cleanly")
    narrate("This is the baseline — most queries are fine, and QuerySentinel")
    narrate("adds near-zero overhead for them.")

    with intercepted:
        cur = intercepted.cursor()
        cur.execute("SELECT id, name, email FROM users ORDER BY id LIMIT 20")
        rows = cur.fetchall()
        print(f"\n  Result: {len(rows)} rows returned normally.")

    # ── Step 2: dangerous query gets auto-rewritten ────────────
    section("STEP 2 — A genuinely dangerous query gets caught and FIXED")
    narrate("This query has 3 correlated subqueries — a classic N+1 performance bug.")
    narrate("Watch QuerySentinel detect it, diagnose WHY it's slow, and have an")
    narrate("LLM agent rewrite it automatically — validated before it ever runs.")

    danger_sql = """
        SELECT u.id, u.name,
            (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
            (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
        FROM users u LIMIT 30
    """

    real_conn2 = psycopg2.connect(**DB_CONFIG)
    log_conn2  = psycopg2.connect(**DB_CONFIG)
    intercepted2 = InterceptedConnection(real_conn2, log_conn=log_conn2, auto_rewrite=True)

    with intercepted2:
        cur2 = intercepted2.cursor()
        try:
            cur2.execute(danger_sql)
            rows = cur2.fetchall()
            print(f"\n  Result: query was auto-fixed and returned {len(rows)} rows.")
        except EscalatedQueryError as e:
            print(f"\n  Result: agent escalated this one for human review: {e}")

    # ── Step 3: self-healing index recommendations ─────────────
    section("STEP 3 — Self-healing: learning from accumulated query history")
    narrate("QuerySentinel doesn't just react query-by-query — it looks at")
    narrate("patterns across ALL logged queries and recommends schema fixes.")

    recs = analyse_and_recommend()
    print_recommendations(recs)

    # ── Step 4: summary numbers ──────────────────────────────────
    section("STEP 4 — Session summary")

    conn3 = psycopg2.connect(**DB_CONFIG)
    with conn3.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM query_logs")
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM query_logs WHERE cost_category = 'DANGER'")
        danger = cur.fetchone()[0]

        cur.execute("""
            SELECT MAX(total_cost) FROM query_logs WHERE total_cost IS NOT NULL
        """)
        max_cost = cur.fetchone()[0]
    conn3.close()

    print(f"""
  Total queries intercepted this session : {total}
  DANGER-category queries detected       : {danger}
  Highest cost query ever seen           : {float(max_cost or 0):.1f}

  What QuerySentinel demonstrates end-to-end:
  --------------------------------------------
  1. Transparent proxy interception        (Week 1)
  2. ML cost prediction before execution   (Week 2)
  3. LLM agent diagnosis + auto-rewrite    (Week 3)
  4. Self-healing index recommendations    (Week 3-4)
  5. Live dashboard + AWS/OCI deployment   (Week 4)
""")

    section("DEMO COMPLETE")
    narrate("Open the dashboard at http://localhost:3000 (or wherever you")
    narrate("served Dashboard.jsx) to show the live visual feed of everything")
    narrate("that just happened.")
    narrate("API docs are auto-generated at http://localhost:8000/docs")


if __name__ == "__main__":
    run_full_demo()
