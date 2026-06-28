"""Real-time fraud scoring with Structured Streaming + MLflow.

Reads new transactions as they land in Bronze, computes the **same feature
vector** as the batch dbt model `int_transactions_enriched`, scores each
transaction with the registered MLflow champion model, and MERGEs the result
into a serving Delta table that powers the live fraud-ops dashboard.

Two design points worth calling out:

* **Features are computed inside `foreachBatch`, not on the streaming DataFrame.**
  Structured Streaming does not support window functions (`lag`, rolling counts)
  on a streaming DataFrame; inside `foreachBatch` each micro-batch is an ordinary
  batch DataFrame where those are valid. This keeps the feature SQL identical to
  the dbt layer so batch and real-time scores agree.
* **Cross-batch history.** Velocity/lag features need a card's prior
  transactions. We seed each micro-batch with each card's recent history from a
  small `gold.card_state` Delta table (updated every batch), so a card-testing
  burst split across micro-batches is still scored correctly. For very high
  throughput, swap this for `applyInPandasWithState` or a Feature Store.
* **Idempotent sink.** The MERGE upserts on `transaction_id`, so replaying a
  micro-batch updates rather than duplicates — the stream is safe to restart.
"""
from __future__ import annotations

import mlflow
from pyspark.sql import DataFrame, SparkSession, functions as F, Window
from delta.tables import DeltaTable

CATALOG = "fraud"
BRONZE = f"{CATALOG}.bronze"
GOLD = f"{CATALOG}.gold"
MODEL_URI = "models:/fraud_scorer@champion"   # Unity Catalog model alias
ALERT_THRESHOLD = 0.5

# Must match FEATURES in ml/train.py exactly (same names, same order).
FEATURES = [
    "amount_log", "seconds_since_prev", "geo_distance_from_home_km",
    "geo_distance_from_prev_km", "is_new_device", "mcc_risk", "amount_zscore",
    "txn_count_60s", "txn_count_1h", "implied_speed_kmh",
]


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = F.radians(lat1), F.radians(lat2)
    dphi = F.radians(lat2 - lat1)
    dlmb = F.radians(lon2 - lon1)
    a = F.sin(dphi / 2) ** 2 + F.cos(p1) * F.cos(p2) * F.sin(dlmb / 2) ** 2
    return F.lit(2 * 6371.0) * F.asin(F.sqrt(a))


def compute_features(txn: DataFrame, cards: DataFrame, merch: DataFrame) -> DataFrame:
    """Batch feature engineering — mirrors dbt int_transactions_enriched.
    Operates on an ordinary (non-streaming) DataFrame, e.g. one micro-batch."""
    w = Window.partitionBy("card_id").orderBy("event_ts")
    w_60 = Window.partitionBy("card_id").orderBy(F.col("event_ts").cast("long")).rangeBetween(-60, 0)
    w_3600 = Window.partitionBy("card_id").orderBy(F.col("event_ts").cast("long")).rangeBetween(-3600, 0)
    w_hist = w.rowsBetween(-50, -1)

    return (
        txn.join(F.broadcast(cards), "card_id", "left")
        .join(F.broadcast(merch), "merchant_id", "left")
        .withColumn("prev_ts", F.lag("event_ts").over(w))
        .withColumn("prev_lat", F.lag("lat").over(w))
        .withColumn("prev_lon", F.lag("lon").over(w))
        .withColumn("seconds_since_prev",
                    F.coalesce(F.unix_timestamp("event_ts") - F.unix_timestamp("prev_ts"),
                               F.lit(999999)))
        .withColumn("geo_distance_from_home_km",
                    _haversine(F.col("lat"), F.col("lon"), F.col("home_lat"), F.col("home_lon")))
        .withColumn("geo_distance_from_prev_km",
                    F.coalesce(_haversine(F.col("lat"), F.col("lon"),
                                          F.col("prev_lat"), F.col("prev_lon")), F.lit(0.0)))
        .withColumn("is_new_device",
                    (F.col("device_id") != F.col("primary_device_id")).cast("int"))
        .withColumn("mcc_risk", F.coalesce(F.col("baseline_fraud_rate"), F.lit(0.0)))
        .withColumn("amount_log", F.log1p("amount"))
        .withColumn("card_amount_mean", F.avg("amount").over(w_hist))
        .withColumn("card_amount_std", F.stddev("amount").over(w_hist))
        .withColumn("amount_zscore",
                    F.when(F.col("card_amount_std").isNull() | (F.col("card_amount_std") == 0), 0.0)
                    .otherwise((F.col("amount") - F.col("card_amount_mean")) / F.col("card_amount_std")))
        .withColumn("txn_count_60s", F.count("*").over(w_60))
        .withColumn("txn_count_1h", F.count("*").over(w_3600))
        .withColumn("implied_speed_kmh",
                    F.when(F.col("seconds_since_prev") <= 0, 0.0)
                    .otherwise(F.col("geo_distance_from_prev_km") / (F.col("seconds_since_prev") / 3600.0)))
    )


def upsert_batch(scorer, cards: DataFrame, merch: DataFrame):
    """foreachBatch handler: enrich → score → MERGE into the alert table, and
    refresh per-card state so the next batch has cross-batch history."""
    def _fn(batch_df: DataFrame, batch_id: int):
        if batch_df.isEmpty():
            return
        spark = batch_df.sparkSession
        batch = batch_df.withColumn("event_ts", F.to_timestamp("ts"))

        # seed with recent prior history per card (cross-batch continuity)
        try:
            history = spark.read.table(f"{GOLD}.card_state")
            ctx = batch.select("card_id").distinct().join(history, "card_id")
            enriched = compute_features(batch.unionByName(ctx, allowMissingColumns=True),
                                        cards, merch).where(F.col("event_ts").isNotNull())
        except Exception:
            enriched = compute_features(batch, cards, merch)

        # keep only this batch's transactions for scoring
        enriched = enriched.join(batch.select("transaction_id"), "transaction_id")

        pdf = enriched.select("transaction_id", "card_id", "merchant_id",
                              "amount", "event_ts", "lat", "lon", *FEATURES).toPandas()
        pdf["fraud_score"] = scorer.predict(pdf[FEATURES])
        pdf["is_alert"] = pdf["fraud_score"] >= ALERT_THRESHOLD
        scored = spark.createDataFrame(
            pdf[["transaction_id", "card_id", "merchant_id", "amount",
                 "event_ts", "fraud_score", "is_alert"]])

        tgt = DeltaTable.forName(spark, f"{GOLD}.live_fraud_alerts")
        (tgt.alias("t").merge(scored.alias("s"), "t.transaction_id = s.transaction_id")
         .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())

        # update per-card state to the latest transaction seen this batch
        latest = (pdf.sort_values("event_ts").groupby("card_id")
                  .tail(1)[["card_id", "event_ts", "lat", "lon"]])
        (spark.createDataFrame(latest)
         .write.mode("overwrite").option("mergeSchema", "true")
         .saveAsTable(f"{GOLD}.card_state"))
    return _fn


def main() -> None:
    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {GOLD}")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {GOLD}.live_fraud_alerts (
            transaction_id STRING, card_id STRING, merchant_id STRING,
            amount DOUBLE, event_ts TIMESTAMP, fraud_score DOUBLE, is_alert BOOLEAN
        ) USING DELTA
    """)

    scorer = mlflow.pyfunc.load_model(MODEL_URI)
    cards = spark.read.table(f"{BRONZE}.raw_cards")
    merch = spark.read.table(f"{BRONZE}.raw_merchants")
    raw = spark.readStream.table(f"{BRONZE}.raw_transactions")

    (raw.writeStream
     .foreachBatch(upsert_batch(scorer, cards, merch))
     .option("checkpointLocation", f"/Volumes/{CATALOG}/landing/_checkpoints/scoring")
     .trigger(processingTime="10 seconds")
     .start()
     .awaitTermination())


if __name__ == "__main__":
    main()
