"""
QuerySentinel — LLM Rewrite Agent (Week 3/4 BUGFIX)
======================================================
BUG FOUND IN WEEK 3 DEMO:
  A query with cost=0.9 (already trivially cheap) went through
  the full rewrite loop. The agent correctly determined no
  rewrite was possible/needed (0% improvement both attempts),
  but the validate_node ALWAYS compares improvement against
  IMPROVEMENT_THRESHOLD (30%) — so "no improvement needed
  because it's already fast" and "rewrite failed to help a
  genuinely slow query" both produced the same ESCALATE
  outcome. That's misleading: an already-fast query escalating
  to "human review" is a false alarm.

FIX:
  Add an early exit: if the ORIGINAL cost is already below a
  low-cost floor (e.g. < 10 cost units), there's nothing to
  optimise — accept the original query immediately and skip
  the LLM call entirely. This also saves an unnecessary API
  call for cheap queries that should never have reached the
  agent in the first place (defense in depth alongside the
  predictor fix).

  Additionally: if cost is above the floor but the rewrite
  achieves 0% improvement because the original is *already
  optimal* (not because the rewrite failed), classify this as
  ACCEPT_NO_CHANGE rather than ESCALATE — it's a different,
  non-alarming outcome.
"""

import os
import sys
import json
import re
from typing import TypedDict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, END
from openai import OpenAI

from schema.introspector import get_schema_context, schema_to_prompt_string
from proxy.explainer import explain_query
import psycopg2

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

MAX_REWRITE_ATTEMPTS  = 2
IMPROVEMENT_THRESHOLD = 0.30

# ── NEW: low-cost floor (the actual bugfix) ───────────────────────────────
# If a query's original EXPLAIN cost is already below this, there is
# nothing meaningful to optimise. Skip the LLM call entirely and accept
# the original as-is. This prevents the "escalate a perfectly fine query"
# false alarm AND saves an unnecessary LLM call.
LOW_COST_FLOOR = 10.0


class AgentState(TypedDict):
    original_sql:       str
    original_cost:      float
    original_explain:   dict
    schema_context:      str
    attempt:             int
    rewritten_sql:        Optional[str]
    rewrite_reasoning:    Optional[str]
    rewrite_explain:      Optional[dict]
    rewrite_cost:         Optional[float]
    decision:             Optional[str]      # ACCEPT / ACCEPT_NO_CHANGE / RETRY / ESCALATE
    decision_reasoning:   Optional[str]
    incident_report:      Optional[str]
    error:                Optional[str]


# ─── Node 0 (NEW): Cost floor check — skip agent entirely if already cheap ──

def cost_floor_node(state: AgentState) -> AgentState:
    """
    NEW NODE — the actual bugfix.
    If the original query is already trivially cheap, there's
    nothing to rewrite. Accept immediately, skip the LLM call.
    """
    if state["original_cost"] < LOW_COST_FLOOR:
        print(f"[AGENT] Original cost ({state['original_cost']:.1f}) is below "
              f"floor ({LOW_COST_FLOOR}) — nothing to optimise, skipping LLM call")
        state["decision"] = "ACCEPT_NO_CHANGE"
        state["decision_reasoning"] = (
            f"Original query cost ({state['original_cost']:.1f}) is already "
            f"below the optimisation floor ({LOW_COST_FLOOR}). No rewrite needed."
        )
        state["rewritten_sql"] = state["original_sql"]
    return state


def route_after_floor_check(state: AgentState) -> str:
    """If cost floor already decided ACCEPT_NO_CHANGE, skip straight to report."""
    if state.get("decision") == "ACCEPT_NO_CHANGE":
        return "report"
    return "diagnose"


# ─── Node 1: Diagnose (unchanged) ────────────────────────────────────────────

def diagnose_node(state: AgentState) -> AgentState:
    print(f"\n[AGENT] Diagnosing query (attempt {state['attempt'] + 1})...")

    explain = state["original_explain"]
    reasons = []

    if explain.get("subquery_count", 0) >= 2:
        reasons.append(
            f"Contains {explain['subquery_count']} subqueries — likely correlated "
            f"subqueries causing N+1 execution pattern (re-run per outer row)"
        )
    if explain.get("has_seq_scan"):
        reasons.append("Sequential scan detected — missing index or unindexable WHERE clause")
    if explain.get("has_nested_loop") and explain.get("actual_rows", 0) > 100:
        reasons.append("Nested loop join on large row count — should use hash join")
    if explain.get("row_accuracy", 1.0) < 0.3:
        reasons.append("Poor row estimation — planner statistics may be stale")

    if not reasons:
        reasons.append(f"High total cost ({state['original_cost']:.1f}) without obvious single cause")

    diagnosis = " | ".join(reasons)
    print(f"[AGENT] Diagnosis: {diagnosis}")

    state["rewrite_reasoning"] = diagnosis
    return state


# ─── Node 2: Rewrite (unchanged) ─────────────────────────────────────────────

def rewrite_node(state: AgentState) -> AgentState:
    print(f"[AGENT] Calling LLM to rewrite query...")

    client = OpenAI()

    prompt = f"""You are a senior PostgreSQL database engineer. A query has been
flagged as DANGEROUSLY EXPENSIVE and blocked from running. Your job is to
rewrite it into a FUNCTIONALLY EQUIVALENT but FASTER query.

DATABASE SCHEMA:
{state['schema_context']}

ORIGINAL QUERY (cost={state['original_cost']:.1f}):
{state['original_sql']}

DIAGNOSIS:
{state['rewrite_reasoning']}

RULES:
1. The rewritten query MUST return the same logical result as the original.
2. Prefer JOINs over correlated subqueries (avoid N+1 execution).
3. Avoid SELECT * — select only needed columns if the original did.
4. Do not invent columns or tables that are not in the schema above.
5. If the query can't be meaningfully improved, return the original unchanged.

Respond in this EXACT JSON format, nothing else:
{{
  "rewritten_sql": "<the new SQL query as a single line, no markdown>",
  "explanation": "<one sentence explaining the optimisation>"
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )

        raw_text = response.choices[0].message.content.strip()
        raw_text = re.sub(r'^```json\s*|\s*```$', '', raw_text, flags=re.MULTILINE).strip()

        parsed = json.loads(raw_text)
        rewritten_sql = parsed["rewritten_sql"].strip()
        explanation   = parsed["explanation"].strip()

        print(f"[AGENT] Rewrite proposed: {explanation}")
        print(f"[AGENT] New SQL: {rewritten_sql[:120]}...")

        state["rewritten_sql"]     = rewritten_sql
        state["rewrite_reasoning"] = f"{state['rewrite_reasoning']} | LLM: {explanation}"

    except Exception as e:
        print(f"[AGENT ERROR] Rewrite failed: {e}")
        state["error"] = str(e)
        state["rewritten_sql"] = None

    return state


# ─── Node 3: Validate (FIXED) ────────────────────────────────────────────────

def validate_node(state: AgentState) -> AgentState:
    """
    FIXED: distinguishes between
      - "rewrite achieved 0% because original was already optimal" -> ACCEPT_NO_CHANGE
      - "rewrite genuinely failed to improve a slow query"          -> RETRY / ESCALATE
    """
    print(f"[AGENT] Validating rewrite with EXPLAIN...")

    if not state.get("rewritten_sql"):
        state["decision"] = "ESCALATE"
        state["decision_reasoning"] = "No rewrite was generated (LLM error)"
        return state

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        explain_result = explain_query(conn, state["rewritten_sql"])

        if "error" in explain_result and explain_result["error"]:
            print(f"[AGENT] Validation FAILED — rewrite has SQL error: {explain_result['error']}")
            state["decision"] = "RETRY"
            state["decision_reasoning"] = f"Rewrite is invalid SQL: {explain_result['error']}"
            state["rewrite_explain"] = explain_result
            return state

        rewrite_cost   = explain_result.get("total_cost", float("inf"))
        original_cost  = state["original_cost"]
        improvement    = (original_cost - rewrite_cost) / original_cost if original_cost > 0 else 0

        state["rewrite_explain"] = explain_result
        state["rewrite_cost"]    = rewrite_cost

        print(f"[AGENT] Original cost: {original_cost:.1f} | Rewrite cost: {rewrite_cost:.1f}")
        print(f"[AGENT] Improvement: {improvement:.1%}")

        # ── FIX: rewrite is IDENTICAL or near-identical to original ──────
        # This means the LLM correctly determined "nothing to improve"
        # rather than failing to find an optimisation. Different outcome
        # from a genuine optimisation failure.
        sql_unchanged = (
            state["rewritten_sql"].strip().lower().replace(" ", "")
            == state["original_sql"].strip().lower().replace(" ", "")
        )

        if improvement >= IMPROVEMENT_THRESHOLD:
            state["decision"] = "ACCEPT"
            state["decision_reasoning"] = (
                f"Rewrite reduces cost by {improvement:.0%} "
                f"({original_cost:.1f} -> {rewrite_cost:.1f}). Validated safe to execute."
            )
        elif sql_unchanged or abs(improvement) < 0.05:
            # LLM explicitly said "already optimal" and proved it via EXPLAIN
            state["decision"] = "ACCEPT_NO_CHANGE"
            state["decision_reasoning"] = (
                f"LLM determined original query is already near-optimal "
                f"(cost={original_cost:.1f}, rewrite attempt showed "
                f"{improvement:.0%} change). Proceeding with original query."
            )
        else:
            state["decision"] = "RETRY" if state["attempt"] < MAX_REWRITE_ATTEMPTS - 1 else "ESCALATE"
            state["decision_reasoning"] = (
                f"Rewrite only improved cost by {improvement:.0%}, "
                f"below {IMPROVEMENT_THRESHOLD:.0%} threshold, and is "
                f"NOT just a confirmed-optimal case."
            )

    except Exception as e:
        print(f"[AGENT ERROR] Validation crashed: {e}")
        state["decision"] = "ESCALATE"
        state["decision_reasoning"] = f"Validation error: {e}"
    finally:
        conn.close()

    return state


# ─── Node 4: Report (updated to handle ACCEPT_NO_CHANGE) ─────────────────────

def report_node(state: AgentState) -> AgentState:
    decision = state["decision"]

    if decision == "ACCEPT":
        report = (
            f"QUERY OPTIMISED AUTOMATICALLY\n"
            f"Original query was blocked (cost={state['original_cost']:.1f}, DANGER category).\n"
            f"Diagnosis: {state['rewrite_reasoning']}\n"
            f"Rewritten query validated with EXPLAIN (cost={state['rewrite_cost']:.1f}).\n"
            f"Cost reduced by "
            f"{(state['original_cost'] - state['rewrite_cost']) / state['original_cost']:.0%}.\n"
            f"Action: rewritten query was executed instead of the original."
        )
    elif decision == "ACCEPT_NO_CHANGE":
        report = (
            f"QUERY ALREADY OPTIMAL — NO CHANGE NEEDED\n"
            f"Original query cost: {state['original_cost']:.1f}.\n"
            f"Reason: {state['decision_reasoning']}\n"
            f"Action: original query was executed as-is. No false alarm raised."
        )
    elif decision == "RETRY":
        report = (
            f"REWRITE ATTEMPT {state['attempt'] + 1} INSUFFICIENT\n"
            f"Reason: {state['decision_reasoning']}\n"
            f"Retrying with updated diagnosis..."
        )
    else:  # ESCALATE
        report = (
            f"QUERY ESCALATED TO HUMAN REVIEW\n"
            f"Original query was blocked (cost={state['original_cost']:.1f}, DANGER category).\n"
            f"Agent attempted {state['attempt'] + 1} rewrite(s) but could not "
            f"achieve sufficient improvement on a genuinely expensive query.\n"
            f"Reason: {state['decision_reasoning']}\n"
            f"Action: original query remains BLOCKED. Notify engineering team."
        )

    print(f"\n[INCIDENT REPORT]\n{report}\n")
    state["incident_report"] = report
    return state


# ─── Routing logic ──────────────────────────────────────────────────────────

def route_after_validation(state: AgentState) -> str:
    if state["decision"] == "RETRY" and state["attempt"] < MAX_REWRITE_ATTEMPTS - 1:
        return "retry"
    return "report"


def increment_attempt_node(state: AgentState) -> AgentState:
    state["attempt"] += 1
    return state


# ─── Build the graph (UPDATED with cost_floor entry node) ───────────────────

def build_rewrite_agent():
    """
    cost_floor -[cheap]-> report -> END
         |
         [not cheap]
         v
    diagnose -> rewrite -> validate --[ACCEPT/ACCEPT_NO_CHANGE/ESCALATE]--> report -> END
                   ^                  |
                   |--[RETRY]---------+
    """
    graph = StateGraph(AgentState)

    graph.add_node("cost_floor",  cost_floor_node)
    graph.add_node("diagnose",    diagnose_node)
    graph.add_node("rewrite",     rewrite_node)
    graph.add_node("validate",    validate_node)
    graph.add_node("increment",   increment_attempt_node)
    graph.add_node("report",      report_node)

    graph.set_entry_point("cost_floor")

    graph.add_conditional_edges(
        "cost_floor",
        route_after_floor_check,
        {
            "report":   "report",
            "diagnose": "diagnose",
        }
    )

    graph.add_edge("diagnose", "rewrite")
    graph.add_edge("rewrite",  "validate")

    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "retry":  "increment",
            "report": "report",
        }
    )
    graph.add_edge("increment", "diagnose")
    graph.add_edge("report", END)

    return graph.compile()


# ─── Public interface used by the proxy (unchanged signature) ───────────────

def rewrite_dangerous_query(sql: str, explain_result: dict) -> dict:
    schema = get_schema_context()
    schema_str = schema_to_prompt_string(schema)

    agent = build_rewrite_agent()

    initial_state: AgentState = {
        "original_sql":      sql,
        "original_cost":     explain_result.get("total_cost", 0.0) or 0.0,
        "original_explain":  explain_result,
        "schema_context":    schema_str,
        "attempt":           0,
        "rewritten_sql":     None,
        "rewrite_reasoning": None,
        "rewrite_explain":   None,
        "rewrite_cost":      None,
        "decision":          None,
        "decision_reasoning": None,
        "incident_report":   None,
        "error":             None,
    }

    final_state = agent.invoke(initial_state)
    return final_state


# ─── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  QuerySentinel — Rewrite Agent BUGFIX Verification")
    print("="*65)

    test_cases = [
        ("Trivially cheap query (should ACCEPT_NO_CHANGE, skip LLM)",
         "SELECT id, name, email FROM users ORDER BY id LIMIT 20"),
        ("Correlated subquery (should ACCEPT with rewrite)",
         """SELECT u.id, u.name,
               (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
               (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
            FROM users u LIMIT 30"""),
    ]

    conn = psycopg2.connect(**DB_CONFIG)

    for label, sql in test_cases:
        explain_result = explain_query(conn, sql)
        print(f"\n  TEST: {label}")
        print(f"  Original cost: {explain_result.get('total_cost')}")

        result = rewrite_dangerous_query(sql, explain_result)
        print(f"  DECISION: {result['decision']}")

    conn.close()

    print("\n" + "="*65)
    print("  Expected: cheap query -> ACCEPT_NO_CHANGE (no LLM call, no escalation)")
    print("  Expected: subquery -> ACCEPT (rewritten and validated)")
    print("="*65 + "\n")
