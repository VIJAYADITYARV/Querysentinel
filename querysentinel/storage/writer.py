"""
QuerySentinel — Storage Writer (Day 4 Upgrade)
================================================
Saves all rich EXPLAIN metrics to TimescaleDB.
Also provides analysis queries used on Day 5.
"""

import json
import psycopg2


def save_query_log(conn, log_entry: dict) -> bool:
    """
    Insert a full query log row into TimescaleDB.
    Saves every metric extracted by explainer.py.
    """
    explain  = log_entry.get("explain", {})
    raw_sql  = log_entry.get("sql", "")
    exec_ms  = log_entry.get("exec_ms", 0.0)

    # Ensure table has all columns (safe to run multiple times)
    _ensure_schema(conn)

    sql = """
        INSERT INTO query_logs (
            raw_sql,
            total_cost,
            startup_cost,
            actual_rows,
            plan_rows,
            node_type,
            all_node_types,
            plan_depth,
            exec_ms,
            actual_total_ms,
            has_seq_scan,
            has_nested_loop,
            has_hash_join,
            has_sort,
            has_index_scan,
            row_accuracy,
            cache_hit_ratio,
            danger_score,
            cost_category,
            subquery_count,
            raw_plan
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
    """

    try:
        with conn.cursor() as cur:
            cur.execute(sql, (
                raw_sql,
                explain.get("total_cost"),
                explain.get("startup_cost"),
                explain.get("actual_rows"),
                explain.get("plan_rows"),
                explain.get("node_type", "UNKNOWN"),
                json.dumps(explain.get("all_node_types", [])),
                explain.get("plan_depth"),
                exec_ms,
                explain.get("actual_total_ms"),
                explain.get("has_seq_scan", False),
                explain.get("has_nested_loop", False),
                explain.get("has_hash_join", False),
                explain.get("has_sort", False),
                explain.get("has_index_scan", False),
                explain.get("row_accuracy"),
                explain.get("cache_hit_ratio"),
                explain.get("danger_score"),
                explain.get("cost_category", "UNKNOWN"),
                explain.get("subquery_count", 0),
                json.dumps(explain.get("raw_plan")) if explain.get("raw_plan") else None,
            ))
        conn.commit()
        return True

    except Exception as e:
        print(f"[STORAGE ERROR] {e}")
        conn.rollback()
        return False


def get_expensive_queries(conn, limit: int = 10) -> list:
    """Top N most expensive queries by total_cost."""
    sql = """
        SELECT
            LEFT(raw_sql, 100)  AS query_preview,
            total_cost,
            actual_rows,
            node_type,
            exec_ms,
            cost_category,
            danger_score,
            has_seq_scan,
            has_nested_loop,
            captured_at
        FROM query_logs
        WHERE total_cost IS NOT NULL
        ORDER BY total_cost DESC
        LIMIT %s
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"[QUERY ERROR] {e}")
        return []


def get_category_breakdown(conn) -> list:
    """Count queries per cost category — Day 5 analysis."""
    sql = """
        SELECT
            cost_category,
            COUNT(*)                            AS total_queries,
            AVG(total_cost)::NUMERIC(10,2)      AS avg_cost,
            AVG(exec_ms)::NUMERIC(10,2)         AS avg_exec_ms,
            SUM(CASE WHEN has_seq_scan THEN 1 ELSE 0 END) AS seq_scans,
            SUM(CASE WHEN has_nested_loop THEN 1 ELSE 0 END) AS nested_loops
        FROM query_logs
        WHERE cost_category IS NOT NULL
        GROUP BY cost_category
        ORDER BY avg_cost DESC
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"[QUERY ERROR] {e}")
        return []


def get_ml_training_export(conn) -> list:
    """
    Export all query logs as ML training features.
    Used at start of Week 2 to train the cost predictor.
    Returns every numeric/boolean field — no raw SQL, no raw plan.
    """
    sql = """
        SELECT
            total_cost,
            startup_cost,
            actual_rows,
            plan_rows,
            plan_depth,
            exec_ms,
            actual_total_ms,
            has_seq_scan::int       AS has_seq_scan,
            has_nested_loop::int    AS has_nested_loop,
            has_hash_join::int      AS has_hash_join,
            has_sort::int           AS has_sort,
            has_index_scan::int     AS has_index_scan,
            row_accuracy,
            cache_hit_ratio,
            subquery_count,
            danger_score,
            cost_category
        FROM query_logs
        WHERE total_cost IS NOT NULL
          AND cost_category != 'UNKNOWN'
        ORDER BY captured_at DESC
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"[EXPORT ERROR] {e}")
        return []


def _ensure_schema(conn):
    """
    Add new columns to query_logs if they don't exist yet.
    Safe to call on every insert — uses IF NOT EXISTS pattern.
    """
    new_columns = [
        ("startup_cost",    "FLOAT"),
        ("plan_rows",       "BIGINT"),
        ("all_node_types",  "JSONB"),
        ("plan_depth",      "INT"),
        ("actual_total_ms", "FLOAT"),
        ("has_seq_scan",    "BOOLEAN DEFAULT FALSE"),
        ("has_nested_loop", "BOOLEAN DEFAULT FALSE"),
        ("has_hash_join",   "BOOLEAN DEFAULT FALSE"),
        ("has_sort",        "BOOLEAN DEFAULT FALSE"),
        ("has_index_scan",  "BOOLEAN DEFAULT FALSE"),
        ("row_accuracy",    "FLOAT"),
        ("cache_hit_ratio", "FLOAT"),
        ("danger_score",    "FLOAT"),
        ("cost_category",   "VARCHAR(20)"),
        ("subquery_count",  "INT DEFAULT 0"),
    ]

    with conn.cursor() as cur:
        for col_name, col_type in new_columns:
            cur.execute(f"""
                ALTER TABLE query_logs
                ADD COLUMN IF NOT EXISTS {col_name} {col_type}
            """)
    conn.commit()
