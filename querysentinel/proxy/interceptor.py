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

from proxy.explainer import explain_query
from storage.writer import save_query_log


class InterceptedConnection:
    """Wraps psycopg2 connection — every query goes through interception."""

    def __init__(self, real_conn, log_conn=None):
        self._conn     = real_conn
        self._log_conn = log_conn   # separate connection to write logs
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
        start = time.perf_counter()

        # 1. Clean the SQL for display
        clean_sql = self._clean_sql(sql, params)

        # 2. Increment counter and print header
        self._parent.intercepted_count += 1
        n = self._parent.intercepted_count
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        print(f"\n{'='*62}")
        print(f"[INTERCEPTED #{n}] {ts}")
        print(f"  SQL : {clean_sql[:110]}{'...' if len(clean_sql) > 110 else ''}")

        # 3. Execute the real query
        self._cur.execute(sql, params)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 4. EXPLAIN ANALYZE on SELECT queries only
        explain_result = {}
        if self._is_select(clean_sql):
            try:
                explain_result = explain_query(self._conn, clean_sql, params)

                cost     = explain_result.get("total_cost")    or 0.0
                node     = explain_result.get("node_type")     or "?"
                rows     = explain_result.get("actual_rows")   or 0
                danger   = explain_result.get("danger_score")  or 0.0
                category = explain_result.get("cost_category") or "?"

                print(f"  COST    : {cost:.2f}  ({category})")
                print(f"  NODE    : {node}")
                print(f"  ROWS    : {rows}")
                print(f"  DANGER  : {danger:.2f}")
                print(f"  TIME    : {elapsed_ms:.2f}ms")

                # Flags
                flags = []
                if explain_result.get("has_seq_scan"):    flags.append("SEQ_SCAN")
                if explain_result.get("has_nested_loop"): flags.append("NESTED_LOOP")
                if explain_result.get("subquery_count", 0) > 2:
                    flags.append("CORRELATED_SUBQUERY")
                if flags:
                    print(f"  FLAGS   : {', '.join(flags)}")

                if category in ("HIGH", "DANGER"):
                    print(f"  [WARNING] {category} COST QUERY - QuerySentinel flags this")

            except Exception as e:
                print(f"  [EXPLAIN ERROR] {e}")

        # ── 5. Persist to TimescaleDB (day 4 onwards) ─────────
        log_entry = {
            "sql": clean_sql,
            "exec_ms": round(elapsed_ms, 3),
            "explain": explain_result,
            "captured_at": datetime.now(timezone.utc),
        }

        log_conn = self._parent._log_conn or self._conn
        try:
            saved = save_query_log(log_conn, log_entry)
            if saved:
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
