"""Production retraining loop (Phase 6).

In production you don't have the generator's ground truth — your trustworthy
label is the *chargeback* that settles weeks after the transaction. This job:

1. Builds a training set whose label is `is_confirmed_fraud` (chargeback-derived)
   for transactions old enough to have a settled outcome (older than the max
   chargeback lag), so labels are mature.
2. Retrains and compares PR-AUC against the current champion.
3. Promotes the new model to the `@champion` alias only if it improves — a
   guardrail against silently shipping a worse model.

Scheduled weekly via Databricks Workflows. Reuses the trainer in train.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import mlflow
import pandas as pd
from mlflow.tracking import MlflowClient

from train import FEATURES, best_threshold  # noqa: E402

MODEL = "fraud_scorer"
LABEL_MATURITY_DAYS = 45   # = max chargeback lag; younger txns lack final labels


def load_mature_labelled_data():
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    cutoff = (date.today() - timedelta(days=LABEL_MATURITY_DAYS)).isoformat()
    cols = FEATURES + ["is_confirmed_fraud", "event_date"]
    pdf = (spark.read.table("fraud.gold.fct_transactions")
           .select(*cols).where(f"event_date <= '{cutoff}'").toPandas())
    pdf = pdf.rename(columns={"is_confirmed_fraud": "label"})
    return pdf


def champion_pr_auc(client: MlflowClient) -> float:
    try:
        mv = client.get_model_version_by_alias(MODEL, "champion")
        run = client.get_run(mv.run_id)
        return run.data.metrics.get("pr_auc", 0.0)
    except Exception:
        return 0.0   # no champion yet — any model is an improvement


def main() -> None:
    from sklearn.metrics import average_precision_score
    from xgboost import XGBClassifier

    df = load_mature_labelled_data()
    df["event_date"] = pd.to_datetime(df["event_date"])
    split = df["event_date"].quantile(0.8)
    tr, te = df[df.event_date <= split], df[df.event_date > split]

    X_tr, y_tr = tr[FEATURES].fillna(0), tr["label"].astype(int)
    X_te, y_te = te[FEATURES].fillna(0), te["label"].astype(int)
    pos = max(int(y_tr.sum()), 1)

    client = MlflowClient()
    incumbent = champion_pr_auc(client)

    mlflow.set_experiment("/fraud/fraud_scorer_retrain")
    with mlflow.start_run():
        model = XGBClassifier(
            n_estimators=400, max_depth=6, learning_rate=0.05,
            scale_pos_weight=(len(y_tr) - pos) / pos, eval_metric="aucpr", n_jobs=-1)
        model.fit(X_tr, y_tr)
        scores = model.predict_proba(X_te)[:, 1]
        pr_auc = average_precision_score(y_te, scores)
        mlflow.log_metric("pr_auc", pr_auc)
        mlflow.log_metric("alert_threshold", best_threshold(y_te, scores))
        info = mlflow.xgboost.log_model(model, "model",
                                        registered_model_name=MODEL).model_uri

        print(f"candidate PR-AUC={pr_auc:.3f}  champion={incumbent:.3f}")
        if pr_auc > incumbent + 0.01:
            version = info.split("/")[-1]
            client.set_registered_model_alias(MODEL, "champion", version)
            print("Promoted new champion.")
        else:
            print("Kept existing champion (no significant improvement).")


if __name__ == "__main__":
    main()
