"""
QuerySentinel — Query Interceptor
===================================
Wraps psycopg2 connection so every SQL query
passes through our logging + EXPLAIN pipeline
before hitting PostgreSQL.

Day 1: intercept + print
Day 3: add EXPLAIN ANALYZE
Day 4: persist to TimescaleDB
"""

import time
import sqlparse
from datetime import datetime, timezone

from explainer import explain_query
from storage.writer import save_query_log


class InterceptedConnection:
    """
    Wraps a real psycopg2 connection.
    Every .execute() call is intercepted, logged,
    and run through EXPLAIN before proceeding.
    """

    def __init__(self, real_conn, log_pool=None):
        self._conn = real_conn
        self._log_pool = log_pool   # TimescaleDB pool (added day 4)
        self.intercepted_count = 0

    def cursor(self, **kwargs):
        """Return an intercepted cursor."""
        real_cursor = self._conn.cursor(**kwargs)
        return InterceptedCursor(real_cursor, self._conn, self)

    def close(self):
        self._conn.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class InterceptedCursor:
    """
    Wraps a real psycopg2 cursor.
    Intercepts execute() to log + explain every query.
    """

    def __init__(self, real_cursor, conn, parent):
        self._cur = real_cursor
        self._conn = conn
        self._parent = parent

    def execute(self, sql, params=None):
        """Intercept every SQL call."""
        start_time = time.perf_counter()

        # ── 1. Parse and clean the SQL ─────────────────────────
        clean_sql = self._clean_sql(sql, params)

        # ── 2. Log to terminal ─────────────────────────────────
        self._parent.intercepted_count += 1
        print(f"\n{'-'*60}")
        print(f"[INTERCEPTED #{self._parent.intercepted_count}] "
              f"{datetime.now(timezone.utc).strftime('%H:%M:%S')}")
        print(f"  SQL: {clean_sql[:120]}{'...' if len(clean_sql) > 120 else ''}")

        # ── 3. Run the actual query ────────────────────────────
        self._cur.execute(sql, params)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # ── 4. Run EXPLAIN ANALYZE (SELECT queries only) ───────
        explain_result = {}
        if self._is_select(clean_sql):
            try:
                explain_result = explain_query(self._conn, clean_sql)
                cost = explain_result.get("total_cost", "?")
                node = explain_result.get("node_type", "?")
                rows = explain_result.get("actual_rows", "?")

                print(f"  COST: {cost:.2f}  |  NODE: {node}  |  ROWS: {rows}")
                print(f"  TIME: {elapsed_ms:.2f}ms")

                # Flag expensive queries immediately
                if explain_result.get("total_cost", 0) > 500:
                    print(f"  [WARNING] HIGH COST QUERY - QuerySentinel will flag this")

            except Exception as e:
                print(f"  [EXPLAIN ERROR] {e}")

        # ── 5. Persist to TimescaleDB (day 4 onwards) ─────────
        log_entry = {
            "sql": clean_sql,
            "exec_ms": round(elapsed_ms, 3),
            "explain": explain_result,
            "captured_at": datetime.now(timezone.utc),
        }

        if self._parent._log_pool:
            try:
                save_query_log(self._parent._log_pool, log_entry)
                print(f"  [SUCCESS] Logged to TimescaleDB")
            except Exception as e:
                print(f"  [LOG ERROR] {e}")

        print(f"{'-'*60}")
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size)

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clean_sql(self, sql, params):
        """Format SQL for readable logging."""
        try:
            formatted = sqlparse.format(
                str(sql),
                reindent=False,
                keyword_case="upper",
                strip_whitespace=True,
            )
            return formatted.strip()
        except Exception:
            return str(sql).strip()

    def _is_select(self, sql):
        """Only EXPLAIN SELECT queries — never INSERT/UPDATE/DELETE."""
        first_word = sql.strip().upper().split()[0] if sql.strip() else ""
        return first_word == "SELECT"
