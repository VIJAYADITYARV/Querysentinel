"""
QuerySentinel — Feature Extractor (Week 2)
===========================================
Converts a raw SQL string into a numeric feature
vector that the ML model can predict cost from.

This runs BEFORE the query executes — that's the key.
Week 1: EXPLAIN ANALYZE ran AFTER execution.
Week 2: ML model predicts cost BEFORE execution.

Features extracted from SQL text alone (no DB needed):
  - Query structural features (joins, subqueries, etc.)
  - Clause presence flags
  - SQL token statistics
  - Keyword danger signals
"""

import re
import sqlparse
from sqlparse.sql import IdentifierList, Identifier, Where
from sqlparse.tokens import Keyword, DML


def extract_features(sql: str) -> dict:
    """
    Extract numeric features from raw SQL string.
    Returns a dict of feature_name -> numeric value.
    These are the same column names as ml_training_data.csv
    so the model trained on CSV data works here directly.

    Args:
        sql: Raw SQL string (SELECT only)

    Returns:
        dict with all numeric features
    """
    sql_upper = sql.upper().strip()
    sql_clean = _clean_sql(sql)

    # ── Structural features ────────────────────────────────────
    join_count       = _count_keyword(sql_upper, r'\bJOIN\b')
    subquery_count   = sql_upper.count('SELECT') - 1   # minus the main SELECT
    where_clause     = int(bool(re.search(r'\bWHERE\b', sql_upper)))
    group_by         = int(bool(re.search(r'\bGROUP\s+BY\b', sql_upper)))
    order_by         = int(bool(re.search(r'\bORDER\s+BY\b', sql_upper)))
    having_clause    = int(bool(re.search(r'\bHAVING\b', sql_upper)))
    limit_clause     = int(bool(re.search(r'\bLIMIT\b', sql_upper)))
    distinct_clause  = int(bool(re.search(r'\bDISTINCT\b', sql_upper)))

    # ── Danger signals ─────────────────────────────────────────
    has_like         = int(bool(re.search(r'\bLIKE\b', sql_upper)))
    has_leading_wild = int(bool(re.search(r"LIKE\s+'%", sql_upper)))  # LIKE '%...'
    has_select_star  = int(bool(re.search(r'SELECT\s+\*', sql_upper)))
    has_cross_join   = int(bool(re.search(r'\bCROSS\s+JOIN\b', sql_upper)))
    has_or_clause    = int(bool(re.search(r'\bOR\b', sql_upper)))
    has_not_in       = int(bool(re.search(r'\bNOT\s+IN\b', sql_upper)))
    has_coalesce     = int(bool(re.search(r'\bCOALESCE\b', sql_upper)))

    # ── Aggregation signals ────────────────────────────────────
    agg_count = sum([
        len(re.findall(r'\bCOUNT\s*\(', sql_upper)),
        len(re.findall(r'\bSUM\s*\(',   sql_upper)),
        len(re.findall(r'\bAVG\s*\(',   sql_upper)),
        len(re.findall(r'\bMAX\s*\(',   sql_upper)),
        len(re.findall(r'\bMIN\s*\(',   sql_upper)),
    ])

    # ── Token statistics ───────────────────────────────────────
    tokens       = sql_clean.split()
    token_count  = len(tokens)
    char_length  = len(sql_clean)

    # ── Estimated complexity score (heuristic) ─────────────────
    # Week 2: model learns better weights. This is fallback only.
    complexity = (
        join_count      * 2.0 +
        subquery_count  * 4.0 +
        has_leading_wild * 3.0 +
        has_cross_join  * 5.0 +
        agg_count       * 0.5 +
        has_not_in      * 2.0 +
        (1 - limit_clause) * 1.0   # no LIMIT = potentially huge result set
    )

    return {
        # Structural
        "join_count":        join_count,
        "subquery_count":    subquery_count,
        "where_clause":      where_clause,
        "group_by":          group_by,
        "order_by":          order_by,
        "having_clause":     having_clause,
        "limit_clause":      limit_clause,
        "distinct_clause":   distinct_clause,

        # Danger flags
        "has_like":          has_like,
        "has_leading_wild":  has_leading_wild,
        "has_select_star":   has_select_star,
        "has_cross_join":    has_cross_join,
        "has_or_clause":     has_or_clause,
        "has_not_in":        has_not_in,

        # Aggregation
        "agg_count":         agg_count,

        # Size signals
        "token_count":       token_count,
        "char_length":       char_length,

        # Derived
        "complexity_score":  round(complexity, 2),
    }


def features_to_vector(features: dict) -> list:
    """
    Convert feature dict to ordered numeric list
    for feeding into sklearn / the ML model.
    Order must match FEATURE_COLUMNS in trainer.py.
    """
    return [features[k] for k in FEATURE_COLUMNS]


# Canonical feature column order — used by both trainer and predictor
FEATURE_COLUMNS = [
    "join_count",
    "subquery_count",
    "where_clause",
    "group_by",
    "order_by",
    "having_clause",
    "limit_clause",
    "distinct_clause",
    "has_like",
    "has_leading_wild",
    "has_select_star",
    "has_cross_join",
    "has_or_clause",
    "has_not_in",
    "agg_count",
    "token_count",
    "char_length",
    "complexity_score",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_keyword(sql_upper: str, pattern: str) -> int:
    return len(re.findall(pattern, sql_upper))


def _clean_sql(sql: str) -> str:
    try:
        return sqlparse.format(
            sql,
            reindent=False,
            keyword_case="upper",
            strip_whitespace=True,
        ).strip()
    except Exception:
        return sql.strip()


# ── Quick test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_queries = [
        ("Simple SELECT",
         "SELECT id, name FROM users ORDER BY id LIMIT 20"),

        ("JOIN query",
         "SELECT o.id, u.name FROM orders o JOIN users u ON u.id = o.user_id LIMIT 50"),

        ("Full table scan LIKE",
         "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'"),

        ("Correlated subquery",
         """SELECT u.id,
            (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders,
            (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
            FROM users u LIMIT 30"""),
    ]

    print("\nFeature Extractor Test")
    print("=" * 60)
    for label, sql in test_queries:
        features = extract_features(sql)
        print(f"\n[{label}]")
        print(f"  subquery_count  : {features['subquery_count']}")
        print(f"  join_count      : {features['join_count']}")
        print(f"  has_leading_wild: {features['has_leading_wild']}")
        print(f"  agg_count       : {features['agg_count']}")
        print(f"  complexity_score: {features['complexity_score']}")
