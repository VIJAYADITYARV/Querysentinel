"""
QuerySentinel — Schema Introspector (Week 3)
===============================================
Before the LLM agent can rewrite a query, it needs
to know the actual database schema — table names,
columns, foreign keys, and existing indexes.

Without this context, the LLM would be rewriting
SQL blind. This module gives it eyes.
"""

import os
import sys
import psycopg2
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}


def get_schema_context(conn=None) -> dict:
    """
    Introspect the live database and return a structured
    schema description: tables, columns, types, foreign keys,
    and existing indexes.

    This dict is serialised into the LLM agent's prompt so it
    can write a rewrite that is actually valid SQL for this DB.
    """
    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**DB_CONFIG)

    try:
        tables = _get_tables(conn)
        schema = {}

        for table in tables:
            schema[table] = {
                "columns":     _get_columns(conn, table),
                "foreign_keys": _get_foreign_keys(conn, table),
                "indexes":     _get_indexes(conn, table),
                "row_count":   _get_row_count(conn, table),
            }

        return schema

    finally:
        if own_conn:
            conn.close()


def schema_to_prompt_string(schema: dict) -> str:
    """
    Convert schema dict into a compact text format
    suitable for inserting into an LLM prompt.
    """
    lines = []
    for table, info in schema.items():
        lines.append(f"\nTABLE: {table} (~{info['row_count']} rows)")

        col_strs = [f"{c['name']} {c['type']}" for c in info["columns"]]
        lines.append(f"  columns: {', '.join(col_strs)}")

        if info["foreign_keys"]:
            fk_strs = [
                f"{fk['column']} -> {fk['references_table']}.{fk['references_column']}"
                for fk in info["foreign_keys"]
            ]
            lines.append(f"  foreign keys: {', '.join(fk_strs)}")
        else:
            lines.append(f"  foreign keys: none")

        if info["indexes"]:
            idx_strs = [idx["name"] for idx in info["indexes"]]
            lines.append(f"  indexes: {', '.join(idx_strs)}")
        else:
            lines.append(f"  indexes: NONE (no indexes besides primary key)")

    return "\n".join(lines)


# ── Internal helpers ────────────────────────────────────────────────────────

def _get_tables(conn) -> list:
    """Get all user tables (excluding system/internal tables)."""
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
          AND table_name NOT IN ('query_logs')
        ORDER BY table_name
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def _get_columns(conn, table: str) -> list:
    """Get column names and types for a table."""
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        return [{"name": row[0], "type": row[1]} for row in cur.fetchall()]


def _get_foreign_keys(conn, table: str) -> list:
    """Get foreign key relationships for a table."""
    sql = """
        SELECT
            kcu.column_name,
            ccu.table_name  AS references_table,
            ccu.column_name AS references_column
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        return [
            {
                "column":             row[0],
                "references_table":   row[1],
                "references_column":  row[2],
            }
            for row in cur.fetchall()
        ]


def _get_indexes(conn, table: str) -> list:
    """Get existing indexes on a table (excluding primary key)."""
    sql = """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
          AND indexname NOT LIKE '%%_pkey'
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        return [{"name": row[0], "definition": row[1]} for row in cur.fetchall()]


def _get_row_count(conn, table: str) -> int:
    """Approximate row count (fast, uses pg_stat)."""
    sql = """
        SELECT reltuples::BIGINT
        FROM pg_class
        WHERE relname = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        result = cur.fetchone()
        return int(result[0]) if result and result[0] else 0


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  QuerySentinel — Schema Introspector Test")
    print("="*60)

    schema = get_schema_context()
    prompt_str = schema_to_prompt_string(schema)

    print(prompt_str)
    print("\n" + "="*60)
    print(f"  Found {len(schema)} tables")
    print(f"  This context will be given to the LLM rewrite agent.")
    print("="*60 + "\n")
