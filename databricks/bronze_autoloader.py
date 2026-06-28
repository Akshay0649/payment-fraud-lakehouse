"""Bronze ingestion via Databricks Auto Loader.

Auto Loader (`cloudFiles`) incrementally and idempotently ingests new JSON files
from cloud storage into a Delta table, tracking processed files in a checkpoint
and evolving the schema as new fields appear. This is the lakehouse replacement
for a hand-rolled "load only new files" batch job.

Run as a Databricks job (Workflows) or notebook. Paths assume a Unity Catalog
volume; adjust `LANDING` / catalog / schema for your workspace.

    Bronze contract: append-only, raw, no business logic. We only add ingestion
    metadata (_ingest_ts, _source_file) so every downstream row is traceable.
"""
from __future__ import annotations

from pyspark.sql import SparkSession, functions as F

CATALOG = "fraud"
BRONZE = f"{CATALOG}.bronze"
LANDING = "/Volumes/fraud/landing/payments"        # UC volume backed by cloud storage
CHECKPOINT = "/Volumes/fraud/landing/_checkpoints"


def ingest(spark: SparkSession, name: str, subpath: str, trigger_once: bool = True) -> None:
    """Stream files from `LANDING/<subpath>` into Delta table `BRONZE.<name>`."""
    reader = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.schemaLocation", f"{CHECKPOINT}/{name}/schema")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("cloudFiles.inferColumnTypes", "true")
        .load(f"{LANDING}/{subpath}")
    )

    enriched = (
        reader
        .withColumn("_ingest_ts", F.current_timestamp())
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )

    writer = (
        enriched.writeStream.format("delta")
        .option("checkpointLocation", f"{CHECKPOINT}/{name}/commit")
        .option("mergeSchema", "true")
        .outputMode("append")
        .toTable(f"{BRONZE}.{name}")
    )

    # trigger=availableNow processes everything pending then stops — ideal for a
    # scheduled batch job. Drop it for an always-on streaming ingest.
    if trigger_once:
        writer.trigger(availableNow=True)
    writer.awaitTermination()


def main() -> None:
    spark = SparkSession.builder.getOrCreate()
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE}")

    ingest(spark, "raw_transactions", "transactions")
    ingest(spark, "raw_auth_events", "auth_events")
    ingest(spark, "raw_chargebacks", "chargebacks")

    # Reference dimensions are small; overwrite as batch snapshots.
    for ref in ("accounts", "cards", "merchants"):
        (spark.read.json(f"{LANDING}/reference/{ref}.json")
         .write.mode("overwrite").saveAsTable(f"{BRONZE}.raw_{ref}"))

    print("Bronze ingestion complete.")


if __name__ == "__main__":
    main()
