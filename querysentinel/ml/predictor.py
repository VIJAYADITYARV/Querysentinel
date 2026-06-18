"""
QuerySentinel — ML Cost Predictor (Week 2 + confidence escalation patch)
===========================================================================
Loads the trained model and predicts query cost
category from SQL text BEFORE the query executes.

PATCH: Confidence-threshold escalation.
With a small training set, the model can be uncertain
on rare patterns (e.g. correlated subqueries). Rather than
trusting a low-confidence point estimate, we escalate
uncertain HIGH predictions to DANGER for safety.

Rule: if predicted category is HIGH AND confidence < 60%,
      escalate to DANGER (safer to over-flag than under-flag).

Usage (standalone test):
    python ml/predictor.py
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

# ── Confidence escalation threshold ────────────────────────────────────────
# If model predicts HIGH but confidence is below this, escalate to DANGER.
# Rationale: safer to over-flag an uncertain query than let a potentially
# dangerous query through on a low-confidence guess.
LOW_CONFIDENCE_THRESHOLD = 0.60


class QueryCostPredictor:
    """
    Predicts query cost category from SQL text alone.
    Loads trained model from ml/model.pkl.
    Falls back to heuristic if model not found.
    Applies confidence-threshold escalation as a safety net.
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
              predicted_category: LOW / MEDIUM / HIGH / DANGER (after escalation)
              raw_predicted_category: the model's original prediction (before escalation)
              confidence:         0.0 – 1.0
              action:             ALLOW / FLAG / BLOCK_AND_REWRITE
              escalated:          True if confidence escalation changed the result
              features:           extracted feature dict
              method:             'ml_model' or 'heuristic'
        """
        features = extract_features(sql)

        if self.model_loaded:
            result = self._predict_with_model(sql, features)
        else:
            result = self._predict_heuristic(sql, features)

        return self._apply_confidence_escalation(result)

    def _apply_confidence_escalation(self, result: dict) -> dict:
        """
        Safety net: if the model predicts HIGH with low confidence,
        escalate to DANGER. An uncertain "maybe expensive" guess
        should be treated as "assume the worst" rather than "allow it."
        """
        category   = result["predicted_category"]
        confidence = result["confidence"]

        result["raw_predicted_category"] = category
        result["escalated"] = False

        if category == "HIGH" and confidence < LOW_CONFIDENCE_THRESHOLD:
            result["predicted_category"] = "DANGER"
            result["action"]             = ACTIONS["DANGER"]
            result["danger_level"]       = DANGER_LEVEL["DANGER"]
            result["escalated"]          = True
            result["escalation_reason"]  = (
                f"Predicted HIGH at {confidence:.0%} confidence "
                f"(below {LOW_CONFIDENCE_THRESHOLD:.0%} threshold) "
                f"— escalated to DANGER for safety"
            )

        return result

    def _predict_with_model(self, sql: str, features: dict) -> dict:
        """Use trained ML model for prediction."""
        try:
            X = []
            for col in self.feature_cols:
                val = features.get(col, 0)
                X.append(float(val) if val is not None else 0.0)

            pred_encoded = self.model.predict([X])[0]
            category     = self.label_encoder.inverse_transform([pred_encoded])[0]

            confidence = 0.0
            if hasattr(self.model, "predict_proba"):
                proba      = self.model.predict_proba([X])[0]
                confidence = round(float(max(proba)), 3)
            elif hasattr(self.model, "decision_function"):
                confidence = 0.75

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
        """Rule-based fallback when model not available."""
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
            "confidence":         0.6,
            "action":             ACTIONS.get(category, "FLAG"),
            "danger_level":       DANGER_LEVEL.get(category, 0),
            "features":           features,
            "method":             "heuristic",
        }

    def is_dangerous(self, sql: str) -> bool:
        result = self.predict(sql)
        return result["predicted_category"] == "DANGER"

    def should_flag(self, sql: str) -> bool:
        result = self.predict(sql)
        return result["danger_level"] >= 2


# ── Singleton instance ─────────────────────────────────────────────────────────
_predictor_instance = None

def get_predictor() -> QueryCostPredictor:
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
    print("  QuerySentinel — ML Predictor Test (with confidence escalation)")
    print("="*65)

    for label, sql in test_queries:
        result = predictor.predict(sql)
        cat     = result["predicted_category"]
        raw_cat = result["raw_predicted_category"]
        conf    = result["confidence"]
        action  = result["action"]
        method  = result["method"]
        escalated = result["escalated"]

        icon = {
            "LOW":    "[OK]    ",
            "MEDIUM": "[OK]    ",
            "HIGH":   "[FLAG]  ",
            "DANGER": "[BLOCK] ",
        }.get(cat, "[?]     ")

        print(f"\n  {icon} {label}")
        print(f"    Raw prediction : {raw_cat} ({conf:.0%} confidence)")
        print(f"    Final category : {cat}")
        if escalated:
            print(f"    [ESCALATED]    : {result['escalation_reason']}")
        print(f"    Action         : {action}")
        print(f"    Method         : {method}")

    print("\n" + "="*65)
    print("  Predictor test complete.")
    print("  Confidence escalation active: HIGH @ <60% conf -> DANGER")
    print("="*65 + "\n")