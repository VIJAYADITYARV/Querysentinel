"""
QuerySentinel — Storage Writer
================================
Persists intercepted query logs to TimescaleDB.
Called by the interceptor after every EXPLAIN run.
"""

import json
import psycopg2


def save_query_log(conn, log_entry: dict) -> bool:
    """
    Insert one query log row into TimescaleDB.

    Args:
        conn:      psycopg2 connection to querysentinel DB
        log_entry: dict from interceptor with sql, exec_ms, explain result

    Returns:
        True if saved successfully, False otherwise
    """
    explain = log_entry.get("explain", {})

    # Safely extract values — explain may be empty if query failed
    total_cost  = explain.get("total_cost")
    actual_rows = explain.get("actual_rows")
    node_type   = explain.get("node_type", "UNKNOWN")
    raw_plan    = explain.get("raw_plan")
    exec_ms     = log_entry.get("exec_ms", 0.0)
    raw_sql     = log_entry.get("sql", "")

    sql = """
        INSERT INTO query_logs
            (raw_sql, total_cost, actual_rows, node_type, exec_ms, raw_plan)
        VALUES
            (%s, %s, %s, %s, %s, %s)
    """

    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                raw_sql,
                total_cost,
                actual_rows,
                node_type,
                exec_ms,
                json.dumps(raw_plan) if raw_plan else None,
            ))
        conn.commit()
        return True

    except Exception as e:
        print(f"[STORAGE ERROR] Failed to save query log: {e}")
        conn.rollback()
        return False


def get_expensive_queries(conn, limit: int = 10) -> list:
    """
    Fetch the most expensive queries logged so far.
    Use this at end of day 5 to find your ML training targets.
    """
    sql = """
        SELECT
            LEFT(raw_sql, 100)  AS query_preview,
            total_cost,
            actual_rows,
            node_type,
            exec_ms,
            captured_at
        FROM query_logs
        WHERE total_cost IS NOT NULL
        ORDER BY total_cost DESC
        LIMIT %s
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
            return [
                {
                    "query_preview": r[0],
                    "total_cost":    r[1],
                    "actual_rows":   r[2],
                    "node_type":     r[3],
                    "exec_ms":       r[4],
                    "captured_at":   str(r[5]),
                }
                for r in rows
            ]
    except Exception as e:
        print(f"[STORAGE ERROR] {e}")
        return []
