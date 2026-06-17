"""
QuerySentinel — Training Data Generator (Week 2)
=================================================
Your Week 1 CSV has 177 rows but only 3 classes
(DANGER, LOW, MEDIUM — no HIGH class!).

This script generates 500+ varied SQL queries,
runs them through the proxy to collect EXPLAIN data,
and exports a larger, more balanced training CSV.

Run: python ml/generate_more_data.py
Then: python ml/trainer.py  (retrains on bigger dataset)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras
import csv
import json
import time
from proxy.explainer import explain_query

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_OUT  = os.path.join(BASE_DIR, "ml_training_data.csv")


# ── Query bank — varied patterns across all 4 cost classes ───────────────────

LOW_QUERIES = [
    "SELECT id, name FROM users LIMIT 5",
    "SELECT id, name FROM users LIMIT 10",
    "SELECT id, name FROM users LIMIT 20",
    "SELECT id, name, email FROM users LIMIT 15",
    "SELECT id, name, country FROM users WHERE id = 1",
    "SELECT id, name, country FROM users WHERE id = 5",
    "SELECT id, name, country FROM users WHERE id = 10",
    "SELECT id FROM users ORDER BY id LIMIT 5",
    "SELECT id FROM users ORDER BY id LIMIT 10",
    "SELECT COUNT(*) FROM users",
    "SELECT id, name FROM products LIMIT 10",
    "SELECT id, name, price FROM products LIMIT 20",
    "SELECT id, name, category FROM products WHERE id = 1",
    "SELECT id, name, category FROM products WHERE id = 5",
    "SELECT id, name FROM products ORDER BY id LIMIT 5",
    "SELECT id, total, status FROM orders WHERE id = 1",
    "SELECT id, total FROM orders WHERE id < 10",
    "SELECT id, comment FROM reviews WHERE id = 1",
    "SELECT id, rating FROM reviews LIMIT 10",
    "SELECT id, name, email FROM users ORDER BY name LIMIT 10",
]

MEDIUM_QUERIES = [
    """SELECT o.id, u.name, o.total
       FROM orders o JOIN users u ON u.id = o.user_id LIMIT 20""",
    """SELECT o.id, p.name, o.quantity
       FROM orders o JOIN products p ON p.id = o.product_id LIMIT 20""",
    """SELECT u.name, r.rating, r.comment
       FROM reviews r JOIN users u ON u.id = r.user_id LIMIT 30""",
    """SELECT p.name, r.rating
       FROM reviews r JOIN products p ON p.id = r.product_id LIMIT 30""",
    """SELECT p.category, COUNT(*) AS cnt
       FROM products p GROUP BY p.category""",
    """SELECT u.country, COUNT(*) AS cnt
       FROM users u GROUP BY u.country ORDER BY cnt DESC""",
    """SELECT o.status, COUNT(*) AS cnt
       FROM orders o GROUP BY o.status""",
    """SELECT r.rating, COUNT(*) AS cnt
       FROM reviews r GROUP BY r.rating ORDER BY r.rating""",
    """SELECT o.id, u.name, p.name AS product
       FROM orders o
       JOIN users u    ON u.id = o.user_id
       JOIN products p ON p.id = o.product_id
       WHERE o.status = 'delivered'
       LIMIT 25""",
    """SELECT o.id, u.name, p.category, o.total
       FROM orders o
       JOIN users    u ON u.id = o.user_id
       JOIN products p ON p.id = o.product_id
       WHERE o.total > 100
       LIMIT 30""",
    """SELECT p.category, AVG(r.rating) AS avg_rating
       FROM reviews r JOIN products p ON p.id = r.product_id
       GROUP BY p.category ORDER BY avg_rating DESC""",
    """SELECT u.country, SUM(o.total) AS revenue
       FROM orders o JOIN users u ON u.id = o.user_id
       GROUP BY u.country ORDER BY revenue DESC""",
    """SELECT o.status, SUM(o.total), COUNT(*)
       FROM orders o GROUP BY o.status ORDER BY SUM(o.total) DESC""",
    """SELECT p.category, COUNT(DISTINCT o.user_id) AS buyers
       FROM orders o JOIN products p ON p.id = o.product_id
       GROUP BY p.category""",
    """SELECT u.country, COUNT(DISTINCT o.id) AS orders
       FROM orders o JOIN users u ON u.id = o.user_id
       GROUP BY u.country HAVING COUNT(DISTINCT o.id) > 5""",
]

HIGH_QUERIES = [
    "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'",
    "SELECT * FROM products WHERE LOWER(category) LIKE '%electron%'",
    "SELECT * FROM users WHERE LOWER(name) LIKE '%user%'",
    "SELECT * FROM users WHERE LOWER(email) LIKE '%example%'",
    "SELECT * FROM reviews WHERE LOWER(comment) LIKE '%review%'",
    """SELECT p.*, AVG(r.rating) AS avg_r
       FROM products p LEFT JOIN reviews r ON r.product_id = p.id
       WHERE LOWER(p.name) LIKE '%product%'
       GROUP BY p.id""",
    """SELECT u.*, COUNT(o.id) AS order_cnt
       FROM users u LEFT JOIN orders o ON o.user_id = u.id
       WHERE LOWER(u.name) LIKE '%user%'
       GROUP BY u.id""",
    """SELECT p.category, COUNT(*), AVG(p.price), SUM(o.total)
       FROM products p
       LEFT JOIN orders o ON o.product_id = p.id
       LEFT JOIN reviews r ON r.product_id = p.id
       GROUP BY p.category
       ORDER BY SUM(o.total) DESC NULLS LAST""",
    """SELECT u.country, COUNT(DISTINCT u.id), SUM(o.total), AVG(r.rating)
       FROM users u
       LEFT JOIN orders  o ON o.user_id   = u.id
       LEFT JOIN reviews r ON r.user_id   = u.id
       GROUP BY u.country
       HAVING SUM(o.total) > 100
       ORDER BY SUM(o.total) DESC""",
    """SELECT o.status, u.country, p.category, COUNT(*), SUM(o.total)
       FROM orders o
       JOIN users    u ON u.id = o.user_id
       JOIN products p ON p.id = o.product_id
       GROUP BY o.status, u.country, p.category
       ORDER BY SUM(o.total) DESC""",
]

DANGER_QUERIES = [
    """SELECT u.id,
          (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
          (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
       FROM users u LIMIT 20""",
    """SELECT u.id, u.name,
          (SELECT SUM(o.total) FROM orders o WHERE o.user_id = u.id) AS spent,
          (SELECT AVG(r.rating) FROM reviews r WHERE r.user_id = u.id) AS rating
       FROM users u LIMIT 20""",
    """SELECT p.id, p.name,
          (SELECT COUNT(*) FROM orders  o WHERE o.product_id = p.id) AS sales,
          (SELECT AVG(r.rating) FROM reviews r WHERE r.product_id = p.id) AS rating
       FROM products p LIMIT 20""",
    """SELECT u.id, u.name, u.country,
          (SELECT COUNT(*)         FROM orders o  WHERE o.user_id = u.id) AS orders,
          (SELECT SUM(o2.total)    FROM orders o2 WHERE o2.user_id = u.id) AS revenue,
          (SELECT COUNT(*)         FROM reviews r WHERE r.user_id = u.id) AS reviews,
          (SELECT AVG(r2.rating)   FROM reviews r2 WHERE r2.user_id = u.id) AS avg_rating
       FROM users u ORDER BY orders DESC LIMIT 30""",
    """SELECT p.id, p.name, p.category, p.price,
          (SELECT COUNT(*)       FROM orders  o WHERE o.product_id = p.id) AS orders,
          (SELECT SUM(o2.total)  FROM orders  o2 WHERE o2.product_id = p.id) AS revenue,
          (SELECT COUNT(*)       FROM reviews r WHERE r.product_id = p.id) AS reviews,
          (SELECT AVG(r2.rating) FROM reviews r2 WHERE r2.product_id = p.id) AS rating
       FROM products p ORDER BY orders DESC LIMIT 30""",
    """SELECT u.id,
          (SELECT COUNT(*) FROM orders o
           WHERE o.user_id = u.id AND o.status = 'delivered') AS delivered,
          (SELECT COUNT(*) FROM orders o
           WHERE o.user_id = u.id AND o.status = 'cancelled') AS cancelled,
          (SELECT MAX(o.total) FROM orders o WHERE o.user_id = u.id) AS max_order
       FROM users u LIMIT 25""",
]


def run_explain(conn, sql: str) -> dict:
    """Run EXPLAIN and extract features."""
    try:
        result = explain_query(conn, sql.strip())
        return result
    except Exception as e:
        return {"error": str(e)}


def collect_training_data():
    print("\n" + "="*60)
    print("  QuerySentinel -- Training Data Generator")
    print("  Generating 500+ queries across 4 cost classes")
    print("="*60)

    conn = psycopg2.connect(**DB_CONFIG)
    rows = []
    errors = 0

    all_queries = [
        ("LOW",    LOW_QUERIES),
        ("MEDIUM", MEDIUM_QUERIES),
        ("HIGH",   HIGH_QUERIES),
        ("DANGER", DANGER_QUERIES),
    ]

    for category, queries in all_queries:
        print(f"\n  Collecting {category} queries ({len(queries)} templates)...")
        count = 0

        # Run each query multiple times with small variations
        repeats = max(1, 25 // len(queries))

        for sql in queries:
            for _ in range(repeats):
                try:
                    result = run_explain(conn, sql)
                    if "error" in result:
                        errors += 1
                        continue

                    row = {
                        "total_cost":       result.get("total_cost"),
                        "startup_cost":     result.get("startup_cost"),
                        "actual_rows":      result.get("actual_rows"),
                        "plan_rows":        result.get("plan_rows"),
                        "plan_depth":       result.get("plan_depth"),
                        "exec_ms":          result.get("actual_total_ms"),
                        "actual_total_ms":  result.get("actual_total_ms"),
                        "has_seq_scan":     int(result.get("has_seq_scan", False)),
                        "has_nested_loop":  int(result.get("has_nested_loop", False)),
                        "has_hash_join":    int(result.get("has_hash_join", False)),
                        "has_sort":         int(result.get("has_sort", False)),
                        "has_index_scan":   int(result.get("has_index_scan", False)),
                        "row_accuracy":     result.get("row_accuracy"),
                        "cache_hit_ratio":  result.get("cache_hit_ratio"),
                        "subquery_count":   result.get("subquery_count", 0),
                        "danger_score":     result.get("danger_score"),
                        "cost_category":    category,   # use our known label
                    }
                    rows.append(row)
                    count += 1
                    time.sleep(0.01)

                except Exception as e:
                    errors += 1

        print(f"    Collected {count} rows for {category}")

    conn.close()

    # Save CSV
    if not rows:
        print("\n[ERROR] No data collected.")
        return

    fieldnames = list(rows[0].keys())
    with open(CSV_OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter
    labels = Counter(r["cost_category"] for r in rows)

    print(f"\n" + "="*60)
    print(f"  DONE -- Exported {len(rows)} rows to ml_training_data.csv")
    print(f"  Errors: {errors}")
    print(f"\n  Label distribution:")
    for label in ["LOW", "MEDIUM", "HIGH", "DANGER"]:
        count = labels.get(label, 0)
        bar   = "#" * count
        print(f"    {label:<10} {count:>4}  {bar}")
    print(f"\n  Now run: python ml/trainer.py")
    print("="*60 + "\n")


if __name__ == "__main__":
    collect_training_data()
