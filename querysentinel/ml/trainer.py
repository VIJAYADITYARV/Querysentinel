"""
QuerySentinel — ML Cost Predictor Trainer (Week 2)
====================================================
Trains a cost category classifier on the data
collected in Week 1 (ml_training_data.csv).

Models trained:
  1. Random Forest (fast, interpretable, good baseline)
  2. Gradient Boosting (higher accuracy)
  3. Best model saved to ml/model.pkl

MLflow tracks every experiment run.

Usage:
    python ml/trainer.py

Output:
    ml/model.pkl          -- best trained model
    ml/label_encoder.pkl  -- label encoder
    ml/feature_names.pkl  -- feature column order
    MLflow UI: mlflow ui (then open http://localhost:5001)
"""

import os
import sys
import json
import pickle
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy  as np
import pandas as pd
import mlflow
import mlflow.sklearn

from sklearn.ensemble         import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model     import LogisticRegression
from sklearn.preprocessing    import LabelEncoder
from sklearn.model_selection  import train_test_split, cross_val_score
from sklearn.metrics          import (
    classification_report,
    confusion_matrix,
    accuracy_score,
)
from sklearn.pipeline         import Pipeline
from sklearn.preprocessing    import StandardScaler

from ml.feature_extractor import FEATURE_COLUMNS

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH    = os.path.join(BASE_DIR, "ml_training_data.csv")
MODEL_DIR   = os.path.join(BASE_DIR, "ml")
MODEL_PATH  = os.path.join(MODEL_DIR, "model.pkl")
ENCODER_PATH= os.path.join(MODEL_DIR, "label_encoder.pkl")
FEATURES_PATH = os.path.join(MODEL_DIR, "feature_names.pkl")


# ── Feature columns from CSV (Week 1 EXPLAIN output) ─────────────────────────
# These are what we have from TimescaleDB
CSV_FEATURE_COLS = [
    "total_cost",
    "startup_cost",
    "actual_rows",
    "plan_rows",
    "plan_depth",
    "exec_ms",
    "actual_total_ms",
    "has_seq_scan",
    "has_nested_loop",
    "has_hash_join",
    "has_sort",
    "has_index_scan",
    "row_accuracy",
    "cache_hit_ratio",
    "subquery_count",
    "danger_score",
]

LABEL_COL = "cost_category"


def load_and_prepare_data(csv_path: str):
    """
    Load ml_training_data.csv and prepare for training.
    Handles missing values, encodes labels.
    """
    print(f"\n[DATA] Loading: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"[DATA] Shape: {df.shape}")
    print(f"[DATA] Columns: {list(df.columns)}")

    # Drop rows with missing label
    df = df.dropna(subset=[LABEL_COL])
    df = df[df[LABEL_COL] != "UNKNOWN"]

    print(f"\n[DATA] Label distribution:")
    print(df[LABEL_COL].value_counts().to_string())

    # Select feature columns that exist in CSV
    available = [c for c in CSV_FEATURE_COLS if c in df.columns]
    print(f"\n[DATA] Using {len(available)} features: {available}")

    X = df[available].copy()
    y = df[LABEL_COL].copy()

    # Fill missing values with column median
    X = X.fillna(X.median(numeric_only=True))

    # Convert boolean columns to int
    for col in X.select_dtypes(include="bool").columns:
        X[col] = X[col].astype(int)

    return X, y, available


def train_and_evaluate(X, y, feature_cols: list):
    """
    Train multiple models, track with MLflow, save best one.
    """
    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)
    classes   = list(le.classes_)
    print(f"\n[LABELS] Classes: {classes}")

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
    )
    print(f"[SPLIT] Train: {len(X_train)} | Test: {len(X_test)}")

    # ── Models to try ─────────────────────────────────────────
    models = {
        "RandomForest": RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=2,
            random_state=42,
            class_weight="balanced",
        ),
        "GradientBoosting": GradientBoostingClassifier(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=5,
            random_state=42,
        ),
        "LogisticRegression": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                max_iter=500,
                random_state=42,
                class_weight="balanced",
            )),
        ]),
    }

    # Set up MLflow
    mlflow.set_tracking_uri("mlruns")
    mlflow.set_experiment("QuerySentinel-CostPredictor")

    best_model      = None
    best_accuracy   = 0.0
    best_name       = ""
    results         = {}

    print("\n" + "="*60)
    print("  Training models...")
    print("="*60)

    for name, model in models.items():
        print(f"\n[MODEL] {name}")

        with mlflow.start_run(run_name=name):
            # Train
            model.fit(X_train, y_train)

            # Evaluate
            y_pred    = model.predict(X_test)
            accuracy  = accuracy_score(y_test, y_pred)
            cv_scores = cross_val_score(model, X, y_encoded, cv=5, scoring="accuracy")

            print(f"  Accuracy   : {accuracy:.4f}")
            print(f"  CV Mean    : {cv_scores.mean():.4f} (+/- {cv_scores.std():.4f})")

            # Classification report
            report = classification_report(
                y_test, y_pred,
                target_names=classes,
                output_dict=True,
            )
            print(f"\n  Classification Report:")
            print(classification_report(y_test, y_pred, target_names=classes))

            # Confusion matrix
            cm = confusion_matrix(y_test, y_pred)
            print(f"  Confusion Matrix:")
            print(f"  Classes: {classes}")
            print(f"  {cm}")

            # Log to MLflow
            mlflow.log_param("model_type",    name)
            mlflow.log_param("n_features",    len(feature_cols))
            mlflow.log_param("train_samples", len(X_train))
            mlflow.log_param("test_samples",  len(X_test))
            mlflow.log_metric("accuracy",     accuracy)
            mlflow.log_metric("cv_mean",      cv_scores.mean())
            mlflow.log_metric("cv_std",       cv_scores.std())

            # Log per-class metrics
            for cls in classes:
                if cls in report:
                    mlflow.log_metric(f"f1_{cls}",        report[cls]["f1-score"])
                    mlflow.log_metric(f"precision_{cls}", report[cls]["precision"])
                    mlflow.log_metric(f"recall_{cls}",    report[cls]["recall"])

            # Feature importance (Random Forest only)
            if name == "RandomForest":
                importances = model.feature_importances_
                feat_imp = dict(zip(feature_cols, importances.tolist()))
                top5 = sorted(feat_imp.items(), key=lambda x: x[1], reverse=True)[:5]
                print(f"\n  Top 5 Feature Importances:")
                for feat, imp in top5:
                    bar = "#" * int(imp * 40)
                    print(f"    {feat:<25} {imp:.4f}  {bar}")
                mlflow.log_dict(feat_imp, "feature_importances.json")

            mlflow.sklearn.log_model(model, name)

            results[name] = accuracy

            # Track best
            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_model    = model
                best_name     = name

    # ── Save best model ────────────────────────────────────────
    print("\n" + "="*60)
    print(f"  BEST MODEL: {best_name}  (accuracy: {best_accuracy:.4f})")
    print("="*60)

    with open(MODEL_PATH,    "wb") as f: pickle.dump(best_model, f)
    with open(ENCODER_PATH,  "wb") as f: pickle.dump(le,         f)
    with open(FEATURES_PATH, "wb") as f: pickle.dump(feature_cols, f)

    print(f"\n  Saved: {MODEL_PATH}")
    print(f"  Saved: {ENCODER_PATH}")
    print(f"  Saved: {FEATURES_PATH}")

    print(f"\n  All experiments tracked in MLflow.")
    print(f"  View with: mlflow ui --port 5001")
    print(f"  Then open: http://localhost:5001")

    return best_model, le, feature_cols, best_accuracy


def main():
    print("\n" + "="*60)
    print("  QuerySentinel — ML Cost Predictor Training")
    print("  Week 2, Day 1")
    print("="*60)

    # Check CSV exists
    if not os.path.exists(CSV_PATH):
        print(f"\n[ERROR] ml_training_data.csv not found at {CSV_PATH}")
        print("  Run analysis.py first to export training data.")
        return

    X, y, feature_cols = load_and_prepare_data(CSV_PATH)

    if len(X) < 10:
        print(f"\n[ERROR] Only {len(X)} rows — need at least 10 to train.")
        print("  Run analysis.py with more rounds to collect more data.")
        return

    model, le, features, accuracy = train_and_evaluate(X, y, feature_cols)

    print("\n" + "="*60)
    print("  TRAINING COMPLETE")
    print("="*60)
    print(f"""
  What you just built:
  - Trained 3 ML models on {len(X)} real query execution plans
  - Best model: accuracy {accuracy:.1%} on held-out test set
  - Tracked all experiments in MLflow
  - Saved model to ml/model.pkl

  What you say in interviews:
  "I trained a cost predictor on 177 labelled query
   execution plans collected by my proxy in Week 1.
   Random Forest achieved {accuracy:.0%} accuracy classifying
   queries as LOW / MEDIUM / HIGH / DANGER.
   All experiments tracked with MLflow."

  Next: python ml/predictor.py
  (integrates model into the proxy for live predictions)
""")


if __name__ == "__main__":
    main()
