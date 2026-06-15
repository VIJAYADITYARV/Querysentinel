"""
QuerySentinel — Query Explainer
=================================
Runs EXPLAIN (ANALYZE, FORMAT JSON) on every
intercepted SELECT query and extracts key metrics.

This data becomes the training dataset for your
ML cost predictor in Week 2.
"""

import json
import psycopg2


def explain_query(conn, sql: str) -> dict:
    """
    Run EXPLAIN ANALYZE on a SELECT query.
    Returns structured metrics extracted from the plan.

    Args:
        conn: psycopg2 connection (same connection as the query)
        sql:  The SELECT SQL string to explain

    Returns:
        dict with total_cost, actual_rows, node_type, raw_plan, etc.
    """
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"

    try:
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            raw = cur.fetchone()[0]          # returns JSON string
            plan_json = json.loads(raw) if isinstance(raw, str) else raw
            top_plan = plan_json[0]["Plan"]

            return _extract_metrics(top_plan, plan_json)

    except Exception as e:
        return {
            "error": str(e),
            "total_cost": None,
            "actual_rows": None,
            "node_type": "UNKNOWN",
            "raw_plan": None,
        }


def _extract_metrics(plan: dict, raw_plan: list) -> dict:
    """
    Pull the most useful fields out of the EXPLAIN JSON.
    These become features for your ML model in Week 2.
    """

    # Recursively find all node types in the plan tree
    all_nodes = _collect_nodes(plan)

    # Detect dangerous patterns
    has_seq_scan       = "Seq Scan" in all_nodes
    has_nested_loop    = "Nested Loop" in all_nodes
    has_hash           = "Hash" in all_nodes or "Hash Join" in all_nodes
    has_sort           = "Sort" in all_nodes
    has_index_scan     = "Index Scan" in all_nodes or "Index Only Scan" in all_nodes

    return {
        # ── Core cost metrics ──────────────────────────────────
        "total_cost":       plan.get("Total Cost", 0.0),
        "startup_cost":     plan.get("Startup Cost", 0.0),
        "actual_rows":      plan.get("Actual Rows", 0),
        "plan_rows":        plan.get("Plan Rows", 0),

        # ── Timing ────────────────────────────────────────────
        "actual_total_ms":  plan.get("Actual Total Time", 0.0),
        "actual_loops":     plan.get("Actual Loops", 1),

        # ── Node classification ────────────────────────────────
        "node_type":        plan.get("Node Type", "Unknown"),
        "all_node_types":   list(set(all_nodes)),

        # ── Dangerous pattern flags (ML features in week 2) ───
        "has_seq_scan":     has_seq_scan,
        "has_nested_loop":  has_nested_loop,
        "has_hash_join":    has_hash,
        "has_sort":         has_sort,
        "has_index_scan":   has_index_scan,

        # ── Buffer stats (cache efficiency) ───────────────────
        "shared_hit_blocks":  plan.get("Shared Hit Blocks", 0),
        "shared_read_blocks": plan.get("Shared Read Blocks", 0),

        # ── Cost category (label for ML training) ─────────────
        "cost_category":    _categorise_cost(plan.get("Total Cost", 0.0)),

        # ── Full raw plan (stored in JSONB in TimescaleDB) ────
        "raw_plan":         raw_plan,
    }


def _categorise_cost(cost: float) -> str:
    """
    Label every query with a cost category.
    This becomes your ML training label in Week 2.

    LOW    → safe, no action needed
    MEDIUM → worth watching
    HIGH   → QuerySentinel should flag
    DANGER → QuerySentinel should block + suggest rewrite
    """
    if cost < 50:
        return "LOW"
    elif cost < 200:
        return "MEDIUM"
    elif cost < 1000:
        return "HIGH"
    else:
        return "DANGER"


def _collect_nodes(plan: dict) -> list:
    """
    Walk the plan tree recursively and collect all node types.
    A single query can have many nodes (Sort → Hash Join → Seq Scan etc.)
    """
    nodes = [plan.get("Node Type", "")]
    for child in plan.get("Plans", []):
        nodes.extend(_collect_nodes(child))
    return nodes
