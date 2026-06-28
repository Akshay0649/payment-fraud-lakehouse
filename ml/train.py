"""Train and register the fraud-scoring model with MLflow.

Reads the engineered features from the Gold fact (`fraud.gold.fct_transactions`)
when running on Databricks, or from a local CSV/Parquet export for offline dev.

Key modelling decisions (the interesting part of fraud ML):
* **Time-based split** — train on the earlier window, evaluate on the later one.
  A random split would leak future behaviour and inflate metrics.
* **Severe class imbalance** (~0.2% positives) — we optimise PR-AUC, not
  accuracy, and weight the positive class so the model doesn't predict "never
  fraud" and call it 99.8% accurate.
* **Strict feature hygiene** — the label and any id columns are never fed in.

Run on Databricks:
    python ml/train.py
Run locally on an export:
    python ml/train.py --source local --path data/fct_transactions.parquet
"""
from __future__ import annotations

import argparse

import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, classification_report,
                             precision_recall_curve, roc_auc_score)

FEATURES = [
    "amount_log", "seconds_since_prev", "geo_distance_km", "is_new_device",
    "mcc_risk", "amount_zscore", "txn_count_60s", "txn_count_1h", "implied_speed_kmh",
]
LABEL = "is_fraud_label"
SPLIT_DATE = "2026-06-22"   # last ~week reserved for evaluation


def load_data(source: str, path: str | None) -> pd.DataFrame:
    if source == "databricks":
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        cols = FEATURES + [LABEL, "event_date"]
        return spark.read.table("fraud.gold.fct_transactions").select(*cols).toPandas()
    if path is None:
        raise SystemExit("--path is required when --source local")
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)


def time_split(df: pd.DataFrame):
    df = df.copy()
    df["event_date"] = pd.to_datetime(df["event_date"]).dt.strftime("%Y-%m-%d")
    train = df[df["event_date"] < SPLIT_DATE]
    test = df[df["event_date"] >= SPLIT_DATE]
    return train, test


def best_threshold(y_true, scores) -> float:
    """Pick the threshold that maximises F1 on the eval set — a defensible
    default the ops team can later move to trade recall for precision."""
    prec, rec, thr = precision_recall_curve(y_true, scores)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros_like(prec), where=(prec + rec) > 0)
    return float(thr[max(np.argmax(f1) - 1, 0)]) if len(thr) else 0.5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["databricks", "local"], default="databricks")
    ap.add_argument("--path", default=None)
    ap.add_argument("--register-as", default="fraud_scorer")
    args = ap.parse_args()

    from xgboost import XGBClassifier  # imported here so --help works without it

    df = load_data(args.source, args.path)
    train, test = time_split(df)
    X_tr, y_tr = train[FEATURES].fillna(0), train[LABEL].astype(int)
    X_te, y_te = test[FEATURES].fillna(0), test[LABEL].astype(int)

    pos = max(int(y_tr.sum()), 1)
    scale = (len(y_tr) - pos) / pos   # balance the positive class

    mlflow.set_experiment("/fraud/fraud_scorer")
    with mlflow.start_run():
        mlflow.log_params({
            "model": "xgboost", "scale_pos_weight": round(scale, 1),
            "n_features": len(FEATURES), "split_date": SPLIT_DATE,
            "train_rows": len(X_tr), "train_positives": pos,
        })

        model = XGBClassifier(
            n_estimators=400, max_depth=6, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.8,
            scale_pos_weight=scale, eval_metric="aucpr", n_jobs=-1,
        )
        model.fit(X_tr, y_tr)

        scores = model.predict_proba(X_te)[:, 1]
        pr_auc = average_precision_score(y_te, scores)
        roc_auc = roc_auc_score(y_te, scores)
        thr = best_threshold(y_te, scores)
        preds = (scores >= thr).astype(int)

        mlflow.log_metric("pr_auc", pr_auc)
        mlflow.log_metric("roc_auc", roc_auc)
        mlflow.log_metric("alert_threshold", thr)
        print(f"PR-AUC={pr_auc:.3f}  ROC-AUC={roc_auc:.3f}  threshold={thr:.3f}")
        print(classification_report(y_te, preds, digits=3))

        mlflow.xgboost.log_model(
            model, artifact_path="model",
            registered_model_name=args.register_as,
            input_example=X_te.head(3),
        )


if __name__ == "__main__":
    main()
