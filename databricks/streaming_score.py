"""Real-time fraud scoring with Structured Streaming + MLflow.

Reads new transactions as they land in Bronze, computes the same features the
batch dbt layer computes (kept in sync via `feature_sql` below), scores each
transaction with the registered MLflow model, and MERGEs the result into a
serving Delta table that powers the live fraud-ops dashboard.

The MERGE makes the sink idempotent: replaying a micro-batch updates rather than
duplicates, so the stream is safe to restart.
"""
from __future__ import annotations

import mlflow
from pyspark.sql import SparkSession, functions as F, Window
from delta.tables import DeltaTable

CATALOG = "fraud"
BRONZE = f"{CATALOG}.bronze"
GOLD = f"{CATALOG}.gold"
MODEL_URI = "models:/fraud_scorer@champion"   # Unity Catalog model alias
ALERT_THRESHOLD = 0.5


def stream_features(spark: SparkSession):
    """Per-transaction features computed over a card-partitioned window.

    Mirrors dbt `int_transactions_enriched`. In production these stay identical
    via a shared feature spec; here they are duplicated deliberately so the file
    is self-contained for review.
    """
    raw = (
        spark.readStream.table(f"{BRONZE}.raw_transactions")
        .withColumn("event_ts", F.to_timestamp("ts"))
    )
    cards = spark.read.table(f"{BRONZE}.raw_cards")
    merch = spark.read.table(f"{BRONZE}.raw_merchants")

    w = Window.partitionBy("card_id").orderBy("event_ts")
    enriched = (
        raw.join(F.broadcast(cards), "card_id", "left")
        .join(F.broadcast(merch), "merchant_id", "left")
        .withColumn("prev_ts", F.lag("event_ts").over(w))
        .withColumn("seconds_since_prev",
                    F.unix_timestamp("event_ts") - F.unix_timestamp("prev_ts"))
        .withColumn("geo_distance_km", _haversine(
            F.col("lat"), F.col("lon"), F.col("home_lat"), F.col("home_lon")))
        .withColumn("is_new_device",
                    (F.col("device_id") != F.col("primary_device_id")).cast("int"))
        .withColumn("mcc_risk", F.col("baseline_fraud_rate"))
        .withColumn("amount_log", F.log1p("amount"))
        .withColumn("implied_speed_kmh",
                    F.col("geo_distance_km") / (F.col("seconds_since_prev") / 3600.0))
        .fillna({"seconds_since_prev": 999999, "geo_distance_km": 0.0,
                 "implied_speed_kmh": 0.0})
    )
    return enriched


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = F.radians(lat1), F.radians(lat2)
    dphi = F.radians(lat2 - lat1)
    dlmb = F.radians(lon2 - lon1)
    a = F.sin(dphi / 2) ** 2 + F.cos(p1) * F.cos(p2) * F.sin(dlmb / 2) ** 2
    return F.lit(2 * 6371.0) * F.asin(F.sqrt(a))


FEATURES = ["amount_log", "seconds_since_prev", "geo_distance_km",
            "is_new_device", "mcc_risk", "implied_speed_kmh"]


def upsert_batch(scorer):
    """Return a foreachBatch handler that scores and MERGEs into the alert table."""
    def _fn(batch_df, batch_id):
        if batch_df.isEmpty():
            return
        pdf = batch_df.select("transaction_id", "card_id", "merchant_id",
                              "amount", "event_ts", *FEATURES).toPandas()
        pdf["fraud_score"] = scorer.predict(pdf[FEATURES])
        pdf["is_alert"] = (pdf["fraud_score"] >= ALERT_THRESHOLD)
        scored = batch_df.sparkSession.createDataFrame(
            pdf[["transaction_id", "card_id", "merchant_id", "amount",
                 "event_ts", "fraud_score", "is_alert"]])

        tgt = DeltaTable.forName(batch_df.sparkSession, f"{GOLD}.live_fraud_alerts")
        (tgt.alias("t").merge(scored.alias("s"), "t.transaction_id = s.transaction_id")
         .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())
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
    features = stream_features(spark)

    (features.writeStream
     .foreachBatch(upsert_batch(scorer))
     .option("checkpointLocation", f"/Volumes/{CATALOG}/landing/_checkpoints/scoring")
     .trigger(processingTime="10 seconds")
     .start()
     .awaitTermination())


if __name__ == "__main__":
    main()
