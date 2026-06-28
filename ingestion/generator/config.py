"""Central configuration for the synthetic payment-stream generator.

Everything that controls the *shape* of the generated world lives here so a
reviewer can change one knob (e.g. ``FRAUD_RATE``) and re-run deterministically.
All randomness is seeded from ``SEED`` for reproducible datasets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class GeneratorConfig:
    # --- reproducibility -----------------------------------------------------
    seed: int = 42

    # --- world size ----------------------------------------------------------
    n_accounts: int = 2_000
    n_merchants: int = 300
    cards_per_account: tuple[int, int] = (1, 3)        # inclusive range

    # --- time window ---------------------------------------------------------
    # Generate `window_days` of history ending at `end_ts`.
    end_ts: datetime = datetime(2026, 6, 29, tzinfo=timezone.utc)
    window_days: int = 30

    # --- legitimate behaviour ------------------------------------------------
    txns_per_card_per_day: float = 1.4                 # Poisson mean
    amount_lognorm_mu: float = 3.2                      # ln(USD); ~e^3.2 ≈ $24 median
    amount_lognorm_sigma: float = 1.05

    # --- fraud ---------------------------------------------------------------
    # Fraction of cards that get compromised at some point in the window.
    # ~0.03 keeps the dataset realistically imbalanced (~0.2% of txns) while
    # leaving enough positives to train/evaluate a model on a full-size run.
    compromised_card_rate: float = 0.03
    # Of confirmed-fraud transactions, the share that later raises a chargeback
    # (the *production* label that arrives weeks late). The rest stay "silent".
    chargeback_recovery_rate: float = 0.80
    # False-positive chargebacks: legit txns a customer disputes anyway.
    false_chargeback_rate: float = 0.0008
    # Days between a fraudulent transaction and its chargeback settling.
    chargeback_lag_days: tuple[int, int] = (10, 45)

    # --- auth decisions ------------------------------------------------------
    base_decline_rate: float = 0.03                     # legit txns randomly declined

    # --- output --------------------------------------------------------------
    output_dir: str = "ingestion/output"
    # Partition transaction files by event date so Auto Loader / Spark can prune.
    partition_by_hour: bool = False

    merchant_categories: tuple[str, ...] = field(default=(
        "grocery", "restaurant", "fuel", "online_retail", "electronics",
        "travel", "entertainment", "pharmacy", "apparel", "crypto_exchange",
        "gambling", "money_transfer",
    ))

    # Categories that carry a structurally higher fraud baseline. Used both to
    # weight collusion fraud AND as a learnable feature (mcc_risk_score).
    high_risk_categories: tuple[str, ...] = field(default=(
        "crypto_exchange", "gambling", "money_transfer", "electronics",
    ))


CONFIG = GeneratorConfig()
