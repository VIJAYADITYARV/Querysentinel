"""
QuerySentinel — ML Cost Predictor (Week 2)
============================================
Loads the trained model and predicts query cost
category from SQL text BEFORE the query executes.

This is the core Week 2 feature:
  Week 1: detect expensive queries AFTER they ran
  Week 2: predict expensive queries BEFORE they run

Usage (standalone test):
    python ml/predictor.py

Used by proxy/interceptor.py automatically.
"""

import os
import sys
import pickle
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.feature_extractor import extract_features, FEATURE_COLUMNS

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH    = os.path.join(BASE_DIR, "ml", "model.pkl")
ENCODER_PATH  = os.path.join(BASE_DIR, "ml", "label_encoder.pkl")
FEATURES_PATH = os.path.join(BASE_DIR, "ml", "feature_names.pkl")

# Cost category action map
ACTIONS = {
    "LOW":    "ALLOW",
    "MEDIUM": "ALLOW",
    "HIGH":   "FLAG",
    "DANGER": "BLOCK_AND_REWRITE",
}

DANGER_LEVEL = {
    "LOW":    0,
    "MEDIUM": 1,
    "HIGH":   2,
    "DANGER": 3,
}


class QueryCostPredictor:
    """
    Predicts query cost category from SQL text alone.
    Loads trained model from ml/model.pkl.
    Falls back to heuristic if model not found.
    """

    def __init__(self):
        self.model         = None
        self.label_encoder = None
        self.feature_cols  = None
        self.model_loaded  = False
        self._load_model()

    def _load_model(self):
        """Load model, encoder, and feature list from disk."""
        try:
            if not os.path.exists(MODEL_PATH):
                print("[PREDICTOR] model.pkl not found — using heuristic fallback")
                return

            with open(MODEL_PATH,    "rb") as f: self.model         = pickle.load(f)
            with open(ENCODER_PATH,  "rb") as f: self.label_encoder = pickle.load(f)
            with open(FEATURES_PATH, "rb") as f: self.feature_cols  = pickle.load(f)

            self.model_loaded = True
            print(f"[PREDICTOR] Model loaded from {MODEL_PATH}")
            print(f"[PREDICTOR] Features: {self.feature_cols}")

        except Exception as e:
            print(f"[PREDICTOR] Failed to load model: {e} — using heuristic")

    def predict(self, sql: str) -> dict:
        """
        Predict cost category for a SQL query.

        Args:
            sql: Raw SQL string

        Returns:
            dict with:
              predicted_category: LOW / MEDIUM / HIGH / DANGER
              confidence:         0.0 – 1.0
              action:             ALLOW / FLAG / BLOCK_AND_REWRITE
              features:           extracted feature dict
              method:             'ml_model' or 'heuristic'
        """
        # Extract features from SQL text
        features = extract_features(sql)

        if self.model_loaded:
            return self._predict_with_model(sql, features)
        else:
            return self._predict_heuristic(sql, features)

    def _predict_with_model(self, sql: str, features: dict) -> dict:
        """Use trained ML model for prediction."""
        try:
            # Build feature vector in the order the model expects
            X = []
            for col in self.feature_cols:
                # Map CSV column names to feature extractor output
                val = features.get(col, 0)
                X.append(float(val) if val is not None else 0.0)

            # Predict
            pred_encoded = self.model.predict([X])[0]
            category     = self.label_encoder.inverse_transform([pred_encoded])[0]

            # Get probability if available
            confidence = 0.0
            if hasattr(self.model, "predict_proba"):
                proba      = self.model.predict_proba([X])[0]
                confidence = round(float(max(proba)), 3)
            elif hasattr(self.model, "decision_function"):
                confidence = 0.75  # default for models without proba

            return {
                "predicted_category": category,
                "confidence":         confidence,
                "action":             ACTIONS.get(category, "FLAG"),
                "danger_level":       DANGER_LEVEL.get(category, 0),
                "features":           features,
                "method":             "ml_model",
            }

        except Exception as e:
            print(f"[PREDICTOR] Model prediction failed: {e} — falling back")
            return self._predict_heuristic(sql, features)

    def _predict_heuristic(self, sql: str, features: dict) -> dict:
        """
        Rule-based fallback when model not available.
        Used before training or if model file missing.
        """
        score = features.get("complexity_score", 0)
        subs  = features.get("subquery_count",   0)
        wild  = features.get("has_leading_wild",  0)
        joins = features.get("join_count",        0)

        if subs >= 2 or score >= 8 or wild:
            category = "DANGER"
        elif subs == 1 or score >= 4 or joins >= 2:
            category = "HIGH"
        elif joins == 1 or score >= 2:
            category = "MEDIUM"
        else:
            category = "LOW"

        return {
            "predicted_category": category,
            "confidence":         0.6,   # heuristic confidence
            "action":             ACTIONS.get(category, "FLAG"),
            "danger_level":       DANGER_LEVEL.get(category, 0),
            "features":           features,
            "method":             "heuristic",
        }

    def is_dangerous(self, sql: str) -> bool:
        """Quick check — is this query predicted DANGER?"""
        result = self.predict(sql)
        return result["predicted_category"] == "DANGER"

    def should_flag(self, sql: str) -> bool:
        """Should this query be flagged (HIGH or DANGER)?"""
        result = self.predict(sql)
        return result["danger_level"] >= 2


# ── Singleton instance — loaded once, reused by proxy ─────────────────────────
_predictor_instance = None

def get_predictor() -> QueryCostPredictor:
    """Get singleton predictor instance."""
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = QueryCostPredictor()
    return _predictor_instance


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    predictor = QueryCostPredictor()

    test_queries = [
        ("Simple SELECT",
         "SELECT id, name FROM users ORDER BY id LIMIT 20"),

        ("JOIN query",
         """SELECT o.id, u.name, p.name AS product
            FROM orders o
            JOIN users    u ON u.id = o.user_id
            JOIN products p ON p.id = o.product_id
            LIMIT 50"""),

        ("GROUP BY aggregation",
         """SELECT p.category, COUNT(o.id) AS orders, SUM(o.total) AS revenue
            FROM orders o
            JOIN products p ON p.id = o.product_id
            GROUP BY p.category
            ORDER BY revenue DESC"""),

        ("Full table scan LIKE",
         "SELECT * FROM products WHERE LOWER(name) LIKE '%product%'"),

        ("Correlated subquery",
         """SELECT u.id, u.name,
               (SELECT COUNT(*) FROM orders  o WHERE o.user_id = u.id) AS orders,
               (SELECT COUNT(*) FROM reviews r WHERE r.user_id = u.id) AS reviews
            FROM users u LIMIT 30"""),
    ]

    print("\n" + "="*65)
    print("  QuerySentinel — ML Predictor Test")
    print("  Predicting cost BEFORE query executes")
    print("="*65)

    for label, sql in test_queries:
        result = predictor.predict(sql)
        cat    = result["predicted_category"]
        conf   = result["confidence"]
        action = result["action"]
        method = result["method"]

        icon = {
            "LOW":    "[OK]    ",
            "MEDIUM": "[OK]    ",
            "HIGH":   "[FLAG]  ",
            "DANGER": "[BLOCK] ",
        }.get(cat, "[?]     ")

        print(f"\n  {icon} {label}")
        print(f"    Category   : {cat}")
        print(f"    Confidence : {conf:.0%}")
        print(f"    Action     : {action}")
        print(f"    Method     : {method}")

    print("\n" + "="*65)
    print("  Predictor test complete.")
    print("  Next: this predictor is now wired into the proxy.")
    print("="*65 + "\n")
