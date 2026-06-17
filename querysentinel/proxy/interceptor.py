"""
QuerySentinel — Interceptor v2 (Week 2)
=========================================
The Week 2 upgrade: ML prediction runs BEFORE
the query executes. DANGER queries are blocked
automatically. HIGH queries are flagged.

Replace proxy/interceptor.py with this file.

Key difference from Week 1:
  Week 1: intercept → execute → EXPLAIN → log
  Week 2: intercept → PREDICT → execute → EXPLAIN → log

The prediction step catches dangerous queries
before any database load is incurred.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import sqlparse
from datetime import datetime, timezone

from proxy.explainer    import explain_query
from storage.writer     import save_query_log
from ml.predictor       import get_predictor


class InterceptedConnection:
    """Wraps psycopg2 connection with ML-powered interception."""

    def __init__(self, real_conn, log_conn=None, block_danger=True):
        self._conn        = real_conn
        self._log_conn    = log_conn
        self.block_danger = block_danger   # set False in development mode
        self.intercepted_count = 0
        self.blocked_count     = 0
        self.flagged_count     = 0

    def cursor(self, **kwargs):
        real_cursor = self._conn.cursor(**kwargs)
        return InterceptedCursor(real_cursor, self._conn, self)

    def close(self):
        self._conn.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def summary(self):
        """Print session summary."""
        print(f"\n[SESSION SUMMARY]")
        print(f"  Intercepted : {self.intercepted_count}")
        print(f"  Flagged     : {self.flagged_count}  (HIGH cost)")
        print(f"  Blocked     : {self.blocked_count}  (DANGER cost)")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.summary()
        self.close()


class DangerousQueryError(Exception):
    """Raised when a DANGER query is blocked by QuerySentinel."""
    pass


class InterceptedCursor:
    """
    Wraps psycopg2 cursor.
    Runs ML prediction before every SELECT.
    """

    def __init__(self, real_cursor, conn, parent):
        self._cur    = real_cursor
        self._conn   = conn
        self._parent = parent
        self._predictor = get_predictor()

    def execute(self, sql, params=None):
        start     = time.perf_counter()
        clean_sql = self._clean_sql(sql)

        self._parent.intercepted_count += 1
        n  = self._parent.intercepted_count
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        print(f"\n{'='*65}")
        print(f"[INTERCEPTED #{n}] {ts}")
        print(f"  SQL : {clean_sql[:110]}{'...' if len(clean_sql) > 110 else ''}")

        # ── ML PREDICTION (runs BEFORE execution) ─────────────
        prediction     = {}
        blocked        = False
        explain_result = {}

        if self._is_select(clean_sql):
            prediction = self._predictor.predict(clean_sql)
            cat        = prediction["predicted_category"]
            conf       = prediction["confidence"]
            action     = prediction["action"]
            method     = prediction["method"]

            print(f"  [ML PREDICT] {cat} (confidence: {conf:.0%}) "
                  f"| action: {action} | via: {method}")

            # Flag HIGH queries
            if cat == "HIGH":
                self._parent.flagged_count += 1
                print(f"  [FLAG] HIGH cost query detected before execution")

            # Block DANGER queries
            if cat == "DANGER" and self._parent.block_danger:
                self._parent.blocked_count += 1
                print(f"  [BLOCKED] DANGER query stopped by QuerySentinel")
                print(f"  [BLOCKED] Reason: predicted cost category = DANGER")
                print(f"  [BLOCKED] Action: query will NOT execute")
                print(f"  [BLOCKED] Fix: rewrite query (Week 3 agent handles this)")
                print(f"{'='*65}")

                # Log the blocked query
                log_entry = {
                    "sql":     clean_sql,
                    "exec_ms": 0.0,
                    "explain": {
                        "cost_category":    "DANGER",
                        "danger_score":     1.0,
                        "node_type":        "BLOCKED",
                        "total_cost":       None,
                        "actual_rows":      None,
                    },
                }
                self._save_log(log_entry)

                raise DangerousQueryError(
                    f"QuerySentinel blocked DANGER query: {clean_sql[:80]}..."
                )

        # ── EXECUTE the query ──────────────────────────────────
        self._cur.execute(sql, params)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # ── EXPLAIN ANALYZE (after execution) ─────────────────
        if self._is_select(clean_sql):
            try:
                explain_result = explain_query(self._conn, clean_sql)
                cost     = explain_result.get("total_cost")    or 0.0
                node     = explain_result.get("node_type")     or "?"
                rows     = explain_result.get("actual_rows")   or 0
                danger   = explain_result.get("danger_score")  or 0.0
                actual_cat = explain_result.get("cost_category") or "?"

                print(f"  [ACTUAL]  cost={cost:.1f} | node={node} | "
                      f"rows={rows} | danger={danger:.2f}")

                # Compare prediction vs actual
                pred_cat = prediction.get("predicted_category", "?")
                match    = "[MATCH]" if pred_cat == actual_cat else "[MISMATCH]"
                print(f"  [COMPARE] predicted={pred_cat} | actual={actual_cat} {match}")

                print(f"  [TIME]    {elapsed_ms:.2f}ms")

                flags = []
                if explain_result.get("has_seq_scan"):    flags.append("SEQ_SCAN")
                if explain_result.get("has_nested_loop"): flags.append("NESTED_LOOP")
                if flags:
                    print(f"  [FLAGS]   {', '.join(flags)}")

            except Exception as e:
                print(f"  [EXPLAIN ERROR] {e}")

        # ── PERSIST to TimescaleDB ─────────────────────────────
        log_entry = {
            "sql":     clean_sql,
            "exec_ms": round(elapsed_ms, 3),
            "explain": {
                **explain_result,
                "ml_predicted_category": prediction.get("predicted_category"),
                "ml_confidence":         prediction.get("confidence"),
                "ml_method":             prediction.get("method"),
            },
        }
        self._save_log(log_entry)

        print(f"{'='*65}")
        return self

    # ── Cursor interface ──────────────────────────────────────────────────────

    def fetchall(self):    return self._cur.fetchall()
    def fetchone(self):    return self._cur.fetchone()
    def fetchmany(self, size=None): return self._cur.fetchmany(size)

    @property
    def description(self): return self._cur.description

    @property
    def rowcount(self):    return self._cur.rowcount

    def close(self):       self._cur.close()

    def __enter__(self):   return self
    def __exit__(self, *a): self.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save_log(self, log_entry: dict):
        log_conn = self._parent._log_conn or self._conn
        try:
            saved = save_query_log(log_conn, log_entry)
            if saved:
                print(f"  [LOGGED]  Saved to TimescaleDB")
        except Exception as e:
            print(f"  [LOG ERROR] {e}")

    def _clean_sql(self, sql) -> str:
        try:
            return sqlparse.format(
                str(sql),
                reindent=False,
                keyword_case="upper",
                strip_whitespace=True,
            ).strip()
        except Exception:
            return str(sql).strip()

    def _is_select(self, sql: str) -> bool:
        first = sql.strip().upper().split()[0] if sql.strip() else ""
        return first == "SELECT"
