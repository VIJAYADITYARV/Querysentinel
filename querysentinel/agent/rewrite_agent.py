"""
QuerySentinel — LLM Rewrite Agent (Week 3)
=============================================
This is the agentic core of QuerySentinel.

When a query is blocked as DANGER, this agent:
  1. READS    — the original SQL, EXPLAIN plan, and DB schema
  2. REASONS  — about WHY the query is slow
  3. REWRITES — proposes a faster equivalent query
  4. VALIDATES — runs EXPLAIN on the rewrite BEFORE trusting it
  5. DECIDES  — accept rewrite, retry, or escalate to human

Built with LangGraph as a state machine — not a single
LLM call. Each step is a distinct node so the reasoning
is traceable and the agent can retry if validation fails.

Requires: OPENAI_API_KEY environment variable
"""

import os
import sys
import json
import re
from typing import TypedDict, Optional
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langgraph.graph import StateGraph, END
import google.generativeai as genai

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = "gemini-2.5-flash"

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

MAX_REWRITE_ATTEMPTS = 2
IMPROVEMENT_THRESHOLD = 0.30   # rewrite must cut cost by at least 30%


# ─── Agent State ────────────────────────────────────────────────────────────

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
    decision:             Optional[str]      # ACCEPT / RETRY / ESCALATE
    decision_reasoning:   Optional[str]
    incident_report:      Optional[str]
    error:                Optional[str]


# ─── Node 1: Diagnose ───────────────────────────────────────────────────────

def diagnose_node(state: AgentState) -> AgentState:
    """
    Reasoning step: WHY is this query slow?
    Uses the EXPLAIN plan + schema to build a diagnosis
    that gets fed into the rewrite prompt.
    """
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


# ─── Node 2: Rewrite ────────────────────────────────────────────────────────

def rewrite_node(state: AgentState) -> AgentState:
    """
    LLM call: given the original SQL, diagnosis, and schema,
    propose a faster equivalent query.
    """
    print(f"[AGENT] Calling LLM to rewrite query...")

    try:
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

        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        if response.candidates:
            print(f"[DEBUG] Finish reason: {response.candidates[0].finish_reason}")
        raw_text = response.text.strip()
        
        # Remove potential markdown formatting
        raw_text = re.sub(r'^```json\s*|\s*```$', '', raw_text, flags=re.MULTILINE).strip()
        print(f"[DEBUG] Raw LLM Response: {raw_text}")
            
        data = json.loads(raw_text, strict=False)
        rewritten_sql = data.get("rewritten_sql", state['original_sql'])
        explanation = data.get("explanation", "No explanation provided by LLM.")

        print(f"[AGENT] Rewrite proposed: {explanation}")
        print(f"[AGENT] New SQL: {rewritten_sql[:120]}...")

        state["rewritten_sql"]     = rewritten_sql
        state["rewrite_reasoning"] = f"{state['rewrite_reasoning']} | LLM: {explanation}"

    except Exception as e:
        print(f"[AGENT ERROR] Rewrite failed: {e}")
        state["error"] = str(e)
        state["rewritten_sql"] = None

    return state


# ─── Node 3: Validate ───────────────────────────────────────────────────────

def validate_node(state: AgentState) -> AgentState:
    """
    CRITICAL SAFETY STEP: never trust the LLM's rewrite blindly.
    Run EXPLAIN on the rewritten query and compare cost.
    Only accept if cost improved AND query is syntactically valid.
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

        if improvement >= IMPROVEMENT_THRESHOLD:
            state["decision"] = "ACCEPT"
            state["decision_reasoning"] = (
                f"Rewrite reduces cost by {improvement:.0%} "
                f"({original_cost:.1f} -> {rewrite_cost:.1f}). Validated safe to execute."
            )
        else:
            state["decision"] = "RETRY" if state["attempt"] < MAX_REWRITE_ATTEMPTS - 1 else "ESCALATE"
            state["decision_reasoning"] = (
                f"Rewrite only improved cost by {improvement:.0%}, "
                f"below {IMPROVEMENT_THRESHOLD:.0%} threshold."
            )

    except Exception as e:
        print(f"[AGENT ERROR] Validation crashed: {e}")
        state["decision"] = "ESCALATE"
        state["decision_reasoning"] = f"Validation error: {e}"
    finally:
        conn.close()

    return state


# ─── Node 4: Decide / Report ────────────────────────────────────────────────

def report_node(state: AgentState) -> AgentState:
    """
    Final node: write a plain-English incident report
    summarising what happened, regardless of outcome.
    """
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
            f"achieve sufficient improvement.\n"
            f"Reason: {state['decision_reasoning']}\n"
            f"Action: original query remains BLOCKED. Notify engineering team."
        )

    print(f"\n[INCIDENT REPORT]\n{report}\n")
    state["incident_report"] = report
    return state


# ─── Routing logic ──────────────────────────────────────────────────────────

def route_after_validation(state: AgentState) -> str:
    """Decide which node to go to next based on validation decision."""
    if state["decision"] == "RETRY" and state["attempt"] < MAX_REWRITE_ATTEMPTS - 1:
        return "retry"
    return "report"


def increment_attempt_node(state: AgentState) -> AgentState:
    """Bump attempt counter before retrying."""
    state["attempt"] += 1
    return state


# ─── Build the graph ────────────────────────────────────────────────────────

def build_rewrite_agent():
    """
    Build the LangGraph state machine:

        diagnose -> rewrite -> validate --[ACCEPT/ESCALATE]--> report -> END
                       ^                  |
                       |--[RETRY]---------+
    """
    graph = StateGraph(AgentState)

    graph.add_node("diagnose",   diagnose_node)
    graph.add_node("rewrite",    rewrite_node)
    graph.add_node("validate",   validate_node)
    graph.add_node("increment",  increment_attempt_node)
    graph.add_node("report",     report_node)

    graph.set_entry_point("diagnose")
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
    graph.add_edge("increment", "diagnose")   # loop back with new attempt
    graph.add_edge("report", END)

    return graph.compile()


# ─── Public interface used by the proxy ────────────────────────────────────

def rewrite_dangerous_query(sql: str, explain_result: dict) -> dict:
    """
    Main entry point called by the proxy when a DANGER query is blocked.

    Args:
        sql:            the original SQL that was blocked
        explain_result: the EXPLAIN output that triggered the block

    Returns:
        dict with decision, rewritten_sql (if accepted), and incident_report
    """
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
    test_sql = """
        SELECT u.id, u.name,
            (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
            (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
        FROM users u LIMIT 30
    """

    conn = psycopg2.connect(**DB_CONFIG)
    explain_result = explain_query(conn, test_sql)
    conn.close()

    print("\n" + "="*65)
    print("  QuerySentinel — LLM Rewrite Agent Test")
    print("="*65)
    print(f"\n  Original cost: {explain_result.get('total_cost')}")

    result = rewrite_dangerous_query(test_sql, explain_result)

    print("\n" + "="*65)
    print(f"  FINAL DECISION: {result['decision']}")
    print("="*65)
    if result["decision"] == "ACCEPT":
        print(f"\n  Use this query instead:\n  {result['rewritten_sql']}")
