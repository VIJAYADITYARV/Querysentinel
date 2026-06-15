"""
QuerySentinel — Query Explainer (Day 3 Upgrade)
=================================================
Deeper EXPLAIN ANALYZE parsing.
Extracts richer features that become ML training
data in Week 2.

New in Day 3:
  - Plan depth measurement (nested query complexity)
  - Per-node cost breakdown
  - Row estimation accuracy (plan_rows vs actual_rows)
  - Join type detection
  - Index usage ratio
"""

import json
import psycopg2


def explain_query(conn, sql: str, params=None) -> dict:
    """
    Run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) on a SELECT.
    Returns rich metrics for logging and ML training.
    """
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"

    try:
        with conn.cursor() as cur:
            cur.execute(explain_sql, params)
            raw = cur.fetchone()[0]
            plan_json = json.loads(raw) if isinstance(raw, str) else raw
            top_plan  = plan_json[0]["Plan"]

            return _extract_rich_metrics(top_plan, plan_json)

    except Exception as e:
        return {
            "error":        str(e),
            "total_cost":   None,
            "actual_rows":  None,
            "node_type":    "UNKNOWN",
            "raw_plan":     None,
            "cost_category": "UNKNOWN",
        }


def _extract_rich_metrics(plan: dict, raw_plan: list) -> dict:
    """
    Extract every useful signal from the EXPLAIN JSON.
    Each field here is a potential ML feature in Week 2.
    """
    # Collect all nodes in the plan tree
    all_nodes   = _collect_nodes(plan)
    node_types  = [n["type"] for n in all_nodes]

    # Plan tree depth = complexity of the query
    plan_depth  = _measure_depth(plan)

    # Row estimation accuracy (bad estimates = slow queries)
    plan_rows   = plan.get("Plan Rows", 1)
    actual_rows = plan.get("Actual Rows", 0)
    row_accuracy = round(
        min(plan_rows, actual_rows) / max(plan_rows, actual_rows, 1), 3
    )

    # Buffer stats — cache misses = expensive disk reads
    shared_hit   = plan.get("Shared Hit Blocks", 0)
    shared_read  = plan.get("Shared Read Blocks", 0)
    total_blocks = shared_hit + shared_read
    cache_hit_ratio = round(
        shared_hit / total_blocks if total_blocks > 0 else 1.0, 3
    )

    # Dangerous pattern flags
    has_seq_scan    = "Seq Scan"    in node_types
    has_nested_loop = "Nested Loop" in node_types
    has_hash_join   = "Hash Join"   in node_types
    has_sort        = "Sort"        in node_types
    has_index       = any(
        "Index" in t for t in node_types
    )

    # Count correlated subquery indicators
    subquery_count = node_types.count("Limit") + node_types.count("Aggregate")

    total_cost = plan.get("Total Cost", 0.0)

    return {
        # ── Core cost ──────────────────────────────────────────
        "total_cost":       total_cost,
        "startup_cost":     plan.get("Startup Cost", 0.0),
        "actual_rows":      actual_rows,
        "plan_rows":        plan_rows,

        # ── Timing ────────────────────────────────────────────
        "actual_total_ms":  plan.get("Actual Total Time", 0.0),
        "actual_loops":     plan.get("Actual Loops", 1),

        # ── Node classification ────────────────────────────────
        "node_type":        plan.get("Node Type", "Unknown"),
        "all_node_types":   list(set(node_types)),
        "total_nodes":      len(all_nodes),
        "plan_depth":       plan_depth,

        # ── Quality signals ────────────────────────────────────
        "row_accuracy":     row_accuracy,
        "cache_hit_ratio":  cache_hit_ratio,
        "subquery_count":   subquery_count,

        # ── Danger flags (ML features) ─────────────────────────
        "has_seq_scan":     has_seq_scan,
        "has_nested_loop":  has_nested_loop,
        "has_hash_join":    has_hash_join,
        "has_sort":         has_sort,
        "has_index_scan":   has_index,

        # ── Derived danger score (simple heuristic) ────────────
        # Week 2 ML model will replace this with learned weights
        "danger_score":     _compute_danger_score(
            total_cost, has_seq_scan, has_nested_loop, subquery_count
        ),

        # ── Cost label (ML training target) ───────────────────
        "cost_category":    _categorise_cost(total_cost),

        # ── Raw plan (stored in JSONB) ─────────────────────────
        "raw_plan":         raw_plan,
    }


def _compute_danger_score(cost, seq_scan, nested_loop, subqueries) -> float:
    """
    Simple 0–1 danger score using heuristics.
    Week 2 ML model will learn better weights from data.
    """
    score = 0.0
    if cost > 1000:     score += 0.5
    elif cost > 500:    score += 0.3
    elif cost > 100:    score += 0.1
    if seq_scan:        score += 0.2
    if nested_loop:     score += 0.2
    score += min(subqueries * 0.05, 0.2)
    return round(min(score, 1.0), 3)


def _categorise_cost(cost: float) -> str:
    """
    4-class label for ML training.
    LOW / MEDIUM / HIGH / DANGER
    """
    if cost is None:    return "UNKNOWN"
    if cost < 50:       return "LOW"
    elif cost < 200:    return "MEDIUM"
    elif cost < 800:    return "HIGH"
    else:               return "DANGER"


def _collect_nodes(plan: dict) -> list:
    """Walk plan tree recursively, collect every node."""
    nodes = [{"type": plan.get("Node Type", ""), "cost": plan.get("Total Cost", 0)}]
    for child in plan.get("Plans", []):
        nodes.extend(_collect_nodes(child))
    return nodes


def _measure_depth(plan: dict, depth: int = 0) -> int:
    """Measure the depth of the plan tree."""
    children = plan.get("Plans", [])
    if not children:
        return depth
    return max(_measure_depth(child, depth + 1) for child in children)
