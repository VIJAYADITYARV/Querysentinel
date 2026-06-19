"""
QuerySentinel — Interceptor v3 (Week 3)
==========================================
The full autonomous loop:

  intercept -> ML predict -> [if DANGER] -> LLM agent rewrites
            -> validate rewrite -> execute rewritten query
            -> log everything (original + rewrite + decision)

Week 1: detect after execution
Week 2: predict before execution, block DANGER
Week 3: don't just block — AUTOMATICALLY FIX and proceed

Replace proxy/interceptor.py with this file (or keep as v3
and import explicitly — your choice).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import sqlparse
from datetime import datetime, timezone

from proxy.explainer  import explain_query
from storage.writer   import save_query_log
from ml.predictor      import get_predictor
from agent.rewrite_agent import rewrite_dangerous_query


class InterceptedConnection:
    """Wraps psycopg2 connection with ML prediction + LLM auto-rewrite."""

    def __init__(self, real_conn, log_conn=None, auto_rewrite=True):
        self._conn        = real_conn
        self._log_conn    = log_conn
        self.auto_rewrite = auto_rewrite   # Week 3: rewrite instead of just blocking
        self.intercepted_count = 0
        self.flagged_count     = 0
        self.blocked_count     = 0
        self.rewritten_count   = 0
        self.escalated_count   = 0

    def cursor(self, **kwargs):
        real_cursor = self._conn.cursor(**kwargs)
        return InterceptedCursor(real_cursor, self._conn, self)

    def close(self):    self._conn.close()
    def commit(self):   self._conn.commit()
    def rollback(self): self._conn.rollback()

    def summary(self):
        print(f"\n[SESSION SUMMARY]")
        print(f"  Intercepted : {self.intercepted_count}")
        print(f"  Flagged     : {self.flagged_count}  (HIGH cost)")
        print(f"  Blocked     : {self.blocked_count}  (DANGER, no auto-rewrite)")
        print(f"  Rewritten   : {self.rewritten_count}  (DANGER -> auto-fixed)")
        print(f"  Escalated   : {self.escalated_count}  (DANGER, rewrite failed)")

    def __enter__(self): return self
    def __exit__(self, *args):
        self.summary()
        self.close()


class EscalatedQueryError(Exception):
    """Raised when agent cannot rewrite a DANGER query successfully."""
    pass


class InterceptedCursor:
    """Wraps psycopg2 cursor with ML prediction + autonomous rewrite."""

    def __init__(self, real_cursor, conn, parent):
        self._cur       = real_cursor
        self._conn       = conn
        self._parent     = parent
        self._predictor  = get_predictor()

    def execute(self, sql, params=None):
        # 1. Clean the SQL for display
        clean_sql = self._clean_sql(sql, params)
        start     = time.perf_counter()

        self._parent.intercepted_count += 1
        n  = self._parent.intercepted_count
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        print(f"\n{'='*65}")
        print(f"[INTERCEPTED #{n}] {ts}")
        print(f"  SQL : {clean_sql[:110]}{'...' if len(clean_sql) > 110 else ''}")

        prediction     = {}
        sql_to_execute = clean_sql      # may get replaced by agent
        explain_result = {}
        agent_result   = None

        if self._is_select(clean_sql):
            prediction = self._predictor.predict(clean_sql)
            cat        = prediction["predicted_category"]
            conf       = prediction["confidence"]

            print(f"  [ML PREDICT] {cat} (confidence: {conf:.0%}) | via: {prediction['method']}")
            if prediction.get("escalated"):
                print(f"  [ESCALATED]  {prediction['escalation_reason']}")

            if cat == "HIGH":
                self._parent.flagged_count += 1
                print(f"  [FLAG] HIGH cost query detected before execution")

            if cat == "DANGER":
                # Need the actual EXPLAIN for the agent's diagnosis
                pre_explain = explain_query(self._conn, clean_sql, params)

                if self._parent.auto_rewrite:
                    print(f"  [AGENT] DANGER query detected — invoking LLM rewrite agent...")
                    try:
                        agent_result = rewrite_dangerous_query(clean_sql, pre_explain)
                        decision = agent_result["decision"]

                        if decision == "ACCEPT":
                            sql_to_execute = agent_result["rewritten_sql"]
                            self._parent.rewritten_count += 1
                            print(f"  [AGENT ACCEPT] Using rewritten query instead")
                            print(f"  [AGENT REASON] {agent_result['decision_reasoning']}")
                        else:
                            self._parent.escalated_count += 1
                            print(f"  [AGENT ESCALATE] Could not safely rewrite query")
                            print(f"  [AGENT REASON] {agent_result['decision_reasoning']}")

                            self._save_log({
                                "sql":     clean_sql,
                                "exec_ms": 0.0,
                                "explain": {
                                    **pre_explain,
                                    "agent_decision": decision,
                                    "agent_report":   agent_result.get("incident_report"),
                                },
                            })
                            print(f"{'='*65}")
                            raise EscalatedQueryError(
                                f"Agent could not safely rewrite query: "
                                f"{agent_result['decision_reasoning']}"
                            )

                    except EscalatedQueryError:
                        raise
                    except Exception as e:
                        print(f"  [AGENT ERROR] {e} — falling back to BLOCK")
                        self._parent.blocked_count += 1
                        self._save_log({
                            "sql": clean_sql, "exec_ms": 0.0,
                            "explain": {**pre_explain, "agent_decision": "ERROR"},
                        })
                        print(f"{'='*65}")
                        raise EscalatedQueryError(f"Agent error: {e}")

                else:
                    # Week 2 behaviour: block without rewriting
                    self._parent.blocked_count += 1
                    print(f"  [BLOCKED] DANGER query stopped (auto_rewrite disabled)")
                    self._save_log({
                        "sql": clean_sql, "exec_ms": 0.0,
                        "explain": pre_explain,
                    })
                    print(f"{'='*65}")
                    raise EscalatedQueryError("Query blocked — auto_rewrite is disabled")

        # ── EXECUTE (original or agent-rewritten SQL) ──────────
        self._cur.execute(sql_to_execute, params)
        elapsed_ms = (time.perf_counter() - start) * 1000

        if self._is_select(sql_to_execute):
            try:
                explain_result = explain_query(self._conn, sql_to_execute, params)
                cost = explain_result.get("total_cost") or 0.0
                node = explain_result.get("node_type")  or "?"
                rows = explain_result.get("actual_rows") or 0
                print(f"  [FINAL]  cost={cost:.1f} | node={node} | rows={rows} | time={elapsed_ms:.2f}ms")
            except Exception as e:
                print(f"  [EXPLAIN ERROR] {e}")

        # ── LOG everything ──────────────────────────────────────
        log_entry = {
            "sql":     sql_to_execute,
            "exec_ms": round(elapsed_ms, 3),
            "explain": {
                **explain_result,
                "was_rewritten":  sql_to_execute != clean_sql,
                "original_sql":   clean_sql if sql_to_execute != clean_sql else None,
                "ml_predicted_category": prediction.get("predicted_category"),
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

    def _clean_sql(self, sql, params) -> str:
        try:
            return sqlparse.format(
                str(sql), reindent=False, keyword_case="upper", strip_whitespace=True,
            ).strip()
        except Exception:
            return str(sql).strip()

    def _is_select(self, sql: str) -> bool:
        first = sql.strip().upper().split()[0] if sql.strip() else ""
        return first == "SELECT"
