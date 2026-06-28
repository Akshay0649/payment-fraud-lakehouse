"""Replay generated transactions as a near-real-time trickle.

Two sinks are supported so the project works with or without infrastructure:

* ``files`` (default) — writes one small JSON file per micro-batch into a
  landing directory that Databricks Auto Loader (or local Spark) watches. No
  broker required; this is the recommended path for a portfolio demo.
* ``kafka`` — publishes to a Kafka topic if you want the extra realism. Requires
  ``confluent-kafka`` and a running broker (see docker-compose.yml).

Run:
    python -m ingestion.streaming_producer --rate 50 --speedup 600
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
import uuid

LANDING = "ingestion/output/streaming_input"


def _load_sorted_txns(src: str) -> list[dict]:
    rows: list[dict] = []
    for path in glob.glob(os.path.join(src, "transactions", "**", "*.jsonl"), recursive=True):
        with open(path, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f)
    rows.sort(key=lambda r: r["ts"])
    return rows


def run_files(rows: list[dict], rate: int) -> None:
    os.makedirs(LANDING, exist_ok=True)
    batch, n = [], 0
    for r in rows:
        batch.append(r)
        if len(batch) >= rate:
            _flush(batch)
            n += len(batch)
            batch = []
            print(f"\r[stream] emitted {n:,} txns", end="", flush=True)
            time.sleep(1.0)
    if batch:
        _flush(batch)
    print(f"\n[stream] done: {n + len(batch):,} txns -> {LANDING}/")


def _flush(batch: list[dict]) -> None:
    fname = os.path.join(LANDING, f"batch-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}.jsonl")
    with open(fname, "w", encoding="utf-8") as f:
        for r in batch:
            f.write(json.dumps(r) + "\n")


def run_kafka(rows: list[dict], topic: str, brokers: str, rate: int) -> None:
    from confluent_kafka import Producer  # lazy import; optional dependency
    p = Producer({"bootstrap.servers": brokers})
    for i, r in enumerate(rows, 1):
        p.produce(topic, key=r["card_id"], value=json.dumps(r))
        if i % rate == 0:
            p.flush()
            time.sleep(1.0)
            print(f"\r[stream] produced {i:,}", end="", flush=True)
    p.flush()
    print(f"\n[stream] done -> kafka topic '{topic}'")


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay transactions as a stream")
    ap.add_argument("--src", default="ingestion/output")
    ap.add_argument("--sink", choices=["files", "kafka"], default="files")
    ap.add_argument("--rate", type=int, default=50, help="transactions per second (wall clock)")
    ap.add_argument("--topic", default="transactions")
    ap.add_argument("--brokers", default="localhost:9092")
    args = ap.parse_args()

    rows = _load_sorted_txns(args.src)
    if not rows:
        raise SystemExit("No transactions found. Run the batch generator first.")
    print(f"[stream] loaded {len(rows):,} transactions; sink={args.sink} rate={args.rate}/s")

    if args.sink == "files":
        run_files(rows, args.rate)
    else:
        run_kafka(rows, args.topic, args.brokers, args.rate)


if __name__ == "__main__":
    main()
