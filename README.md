# Real-Time Payment Fraud Analytics Platform

A production-grade, end-to-end data platform that turns a raw stream of card
transactions into **real-time fraud scores** and **batch risk analytics** — built
on the Databricks Lakehouse, modelled with dbt, and scored with MLflow.

> **The idea in one line:** a fraud platform is only useful if it works at *two*
> latencies — block the bad transaction in seconds, *and* let analysts understand
> and tune the system over hours. This project does both off a single Delta
> storage layer, which is exactly what a lakehouse is for.

```
 generator ─► Auto Loader ─► BRONZE ─► dbt (SILVER ─► GOLD) ─► BI dashboards
                  Δ Delta        │            ▲                      ▲
 streaming ───────────────────►  ┘   Structured Streaming + MLflow ──┘
 producer                            (real-time scores MERGE into Gold)
```

See [`docs/architecture.md`](docs/architecture.md) for the full design and the
reasoning behind it.

---

## Why this project is interesting (the problem-solving)

Most "dbt on a warehouse" demos move clean rows from A to B. Payment fraud forces
the genuinely hard problems, and this repo tackles each one explicitly:

| Hard problem | How it's solved here |
|---|---|
| **You need labels, but real fraud labels are messy** | A generator injects 5 named fraud patterns with *ground-truth* labels. The label lives in Bronze/Silver for evaluation only and is **never** a model feature — so the supervised setup is honest. |
| **Real labels arrive weeks late** | Chargebacks settle 10–45 days after the fact. They're modelled as a separate late-arriving stream and become the *production* label that drives weekly retraining. |
| **Two latencies, one truth** | Batch (dbt) and streaming (Spark) read/write the **same Delta tables**. Features are defined once and reused on both paths so scores agree. |
| **Severe class imbalance (~0.2%)** | The model optimises **PR-AUC**, weights the positive class, and picks an F1-optimal alert threshold — never "predict no-fraud, claim 99.8% accuracy". |
| **Idempotency & replay** | Auto Loader checkpoints dedupe file ingestion; the streaming sink uses a Delta **MERGE** so re-processing updates instead of duplicating. |
| **Data you can trust** | Source freshness, schema/relationship tests, and singular cross-model invariants (e.g. *no chargeback can predate its transaction*) fail CI before bad data reaches Gold. |

---

## Stack

| Layer | Technology |
|---|---|
| Storage / warehouse | **Databricks Lakehouse** (Delta Lake, Unity Catalog) |
| Ingestion | **Auto Loader** (`cloudFiles`) — incremental, schema-evolving |
| Transformation | **dbt** (`dbt-databricks`), medallion Bronze/Silver/Gold |
| Streaming | **Spark Structured Streaming** + `foreachBatch` MERGE |
| ML | **MLflow** (tracking + Model Registry), XGBoost |
| Orchestration | **Databricks Workflows** via Asset Bundles |
| BI | **Databricks SQL** dashboards (queries in `dashboards/`) |
| CI/CD | **GitHub Actions** — ruff, pytest, sqlfluff, `dbt build` |

---

## Repository layout

```
ingestion/
  generator/            # synthetic payment world + 5 fraud-pattern injectors
  streaming_producer.py # replays transactions as a near-real-time trickle
databricks/
  bronze_autoloader.py  # Auto Loader → Bronze Delta
  streaming_score.py    # Structured Streaming + MLflow → live_fraud_alerts (MERGE)
  databricks.yml        # Asset Bundle: batch, streaming & retrain jobs + schedules
dbt/
  models/staging/       # typed 1:1 views (Silver)
  models/intermediate/  # int_transactions_enriched — the feature vector
  models/marts/         # fct_transactions + aggregates (Gold)
  snapshots/            # SCD2 history for cards
  tests/                # singular cross-model invariants
ml/
  train.py              # MLflow training (time split, imbalance-aware)
  retrain_from_chargebacks.py  # production feedback loop with champion gating
dashboards/queries.sql  # headline dashboard tiles
tests/                  # pytest invariants for the generator
docs/architecture.md
```

---

## Quickstart

### 1. Generate data (runs anywhere — no Databricks needed)

```bash
pip install -r requirements-dev.txt
python -m ingestion.generator.generate            # full world (~125k txns, 30 days)
# or a quick one:
python -m ingestion.generator.generate --accounts 500 --window-days 14
```

Outputs partitioned JSON under `ingestion/output/` (transactions, auth events,
chargebacks, and reference dimensions) — exactly what Auto Loader expects.

Run the invariant tests:

```bash
python -m pytest tests/ -q
```

### 2. Land it in the Lakehouse

Upload `ingestion/output/` to a Unity Catalog volume (or cloud storage), then run
the Bronze job:

```bash
databricks bundle deploy -t dev
databricks bundle run fraud_pipeline -t dev      # bronze → dbt build → train
```

### 3. Build the warehouse with dbt

```bash
cd dbt
cp profiles.yml.example ~/.dbt/profiles.yml      # set DBT_DATABRICKS_* env vars
dbt deps && dbt build                            # staging → marts + 40+ tests
dbt docs generate && dbt docs serve              # lineage graph & docs
```

### 4. Train and score

```bash
python ml/train.py                               # logs to MLflow, registers fraud_scorer
# promote the best run to @champion in the MLflow UI, then start scoring:
python -m ingestion.streaming_producer --rate 50 &   # trickle events in
# (on Databricks) run databricks/streaming_score.py as a continuous job
```

> **No Databricks yet?** The generator, tests, and ML training (`--source local`
> on an exported feature file) all run locally. The Lakehouse layers need a
> workspace — the free **Databricks Community / trial** edition is enough.

---

## What the dashboards show

- Daily fraud rate and dollars at risk
- Fraud-by-hour heatmap (the generator builds in realistic diurnal seasonality)
- Top-20 riskiest merchants vs. their baseline
- Chargeback exposure: confirmed loss vs. outstanding (fraud not yet charged back)
- **Model precision/recall trend** — proof the scoring actually works
- **Live alert queue** — real-time tile fed by the streaming job

---

## Data quality

- **Source freshness** SLAs on Bronze (`warn_after` 12h / `error_after` 24h)
- **Schema tests** — `not_null`, `unique`, `relationships`, `accepted_values`,
  `accepted_range` across staging and marts (40+ tests)
- **Singular invariants** in `dbt/tests/` — business rules a column test can't
  express, e.g. chargebacks never predate transactions, every fact row carries a
  complete feature vector, overall fraud rate stays in a plausible band
- **Python invariants** in `tests/` — keys unique, datasets reproducible by seed,
  fraud injected and rare, chargebacks reference real transactions

All of the above run on every pull request via GitHub Actions.

---

## Design notes & extensions

- **Multi-currency / FX** — amounts are USD today; adding an FX dimension is a
  natural Silver extension.
- **Kafka** — `streaming_producer.py` supports a Kafka sink for extra realism;
  Auto Loader file-trigger is the default so the project runs with no broker.
- **Feature store** — the feature logic is duplicated in dbt and the streaming
  job deliberately (each file is self-contained for review); a Databricks Feature
  Store table is the production way to keep them DRY.
