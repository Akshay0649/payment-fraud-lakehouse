# Architecture

## Why this design

The platform answers one operational question — *"is this payment fraud?"* — at
two very different latencies, and a fraud platform that only does one is only
half useful:

| Need | Latency | Path |
|------|---------|------|
| Block/flag a transaction as it happens | seconds | Structured Streaming + MLflow |
| Understand fraud trends, tune thresholds, report losses | hours | Auto Loader → dbt → Gold |

Both paths read and write the **same Delta tables**, so the lakehouse is the
single source of truth — there is no separate "real-time store" to keep in sync.
That is the core reason to build this on Databricks rather than a classic
warehouse: one storage layer, two compute patterns.

## Data flow

```
                ┌───────────────────────── BATCH ─────────────────────────┐
 generator ──►  cloud storage (JSON, partitioned by date)
   + streaming      │
   producer         ▼  Auto Loader (cloudFiles, schema evolution, checkpoint)
                BRONZE  fraud.bronze.raw_{transactions,auth_events,chargebacks,
                  │       accounts,cards,merchants}      [append-only, +ingest metadata]
                  ▼  dbt (dbt-databricks)
                SILVER  stg_* (typed views) → int_transactions_enriched (features)
                  │     + card_snapshot (SCD2)
                  ▼
                 GOLD   fct_transactions (incremental)
                  │     ├─ agg_fraud_by_hour
                  │     ├─ agg_merchant_risk
                  │     ├─ mart_chargeback_exposure
                  │     └─ mart_model_performance ◄── joins live scores to truth
                  ▼
            Databricks SQL dashboard

                ┌─────────────────────── REAL-TIME ───────────────────────┐
 stream  ──►  BRONZE.raw_transactions (readStream)
                  │  same feature logic as int_transactions_enriched
                  ▼  MLflow champion model (pyfunc)
            GOLD.live_fraud_alerts  ◄── idempotent MERGE upsert
                  ▼
            live alert queue tile  +  feeds mart_model_performance

                ┌─────────────────────── FEEDBACK ────────────────────────┐
 chargebacks (settle weeks later) ──► fct_transactions.is_confirmed_fraud
                  ▼  weekly job
            retrain_from_chargebacks.py ──► promote champion if PR-AUC improves
```

## Medallion contracts

- **Bronze** — raw, append-only, no business logic. Only ingestion metadata
  (`_ingest_ts`, `_source_file`) is added, so every row is traceable to a file.
- **Silver** — typed, deduplicated, conformed. Staging is strictly 1:1 with
  source; the only enrichment is `int_transactions_enriched`, where the feature
  vector is computed once and reused by both batch and streaming scoring.
- **Gold** — business-grain facts and aggregates that BI and ML read directly.

## The one rule that makes the ML honest

The generator emits a ground-truth `is_fraud` label. It lives in Bronze and
Silver **only for training and evaluation** — it is never part of the feature
set the model sees (`FEATURES` in `ml/train.py` and `databricks/streaming_score.py`).
In production the trustworthy label is the **chargeback**, which arrives weeks
late; `retrain_from_chargebacks.py` closes that loop and only promotes a new
model when it measurably beats the incumbent on PR-AUC.

## Governance

Unity Catalog provides the `fraud` catalog with `bronze` / `silver` / `gold`
schemas, column-level lineage across Auto Loader → dbt → MLflow, and grants
(e.g. analysts read Gold, never Bronze PII). The MLflow Model Registry holds the
`fraud_scorer` model with a `@champion` alias the streaming job loads by
reference, so model promotion needs no code deploy.

## Feature reference

| Feature | Captures | Fraud pattern it exposes |
|---------|----------|--------------------------|
| `amount_log` | transaction size | amount anomaly |
| `amount_zscore` | size vs. the card's own history | amount anomaly |
| `seconds_since_prev` | time since last txn | card testing (tiny) |
| `txn_count_60s` / `txn_count_1h` | velocity | card testing |
| `geo_distance_from_home_km` | distance from card home | account takeover |
| `geo_distance_from_prev_km` | distance from previous txn | geo-impossible travel |
| `implied_speed_kmh` | prev-distance ÷ time | geo-impossible travel |
| `is_new_device` | unrecognised device | account takeover |
| `mcc_risk` | merchant category risk | merchant collusion |

> Note: two complementary geo features are kept. Distance *from home* flags
> account takeover (a charge in a far city). Distance and implied speed relative
> to the **previous** transaction flag impossible travel and stay robust even
> when a legitimate cardholder genuinely travels — which is why both matter.
