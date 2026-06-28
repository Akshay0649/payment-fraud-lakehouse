"""Invariant tests for the synthetic generator.

These assert properties that downstream dbt models and the ML pipeline depend on,
so a regression in data generation fails CI before it pollutes the warehouse.
"""
from __future__ import annotations

import dataclasses
import random

from ingestion.generator.config import CONFIG
from ingestion.generator.entities import build_world, haversine_km
from ingestion.generator.generate import (build_auth_events, build_chargebacks,
                                          generate_legit, inject_fraud)


def _small_world(seed: int = 7):
    cfg = dataclasses.replace(CONFIG, n_accounts=300, n_merchants=60, window_days=7,
                              seed=seed, compromised_card_rate=0.05)
    rng = random.Random(cfg.seed)
    accounts, cards, merchants = build_world(cfg, rng)
    start = cfg.end_ts.timestamp() - cfg.window_days * 86400
    txns = []
    for c in cards:
        txns.extend(generate_legit(c, merchants, cfg, rng, start))
    fraud, n_comp = inject_fraud(cards, merchants, cfg, rng, start)
    txns.extend(fraud)
    return cfg, rng, accounts, cards, merchants, txns, n_comp


def test_world_keys_are_unique():
    _, _, accounts, cards, merchants, _, _ = _small_world()
    assert len({a.account_id for a in accounts}) == len(accounts)
    assert len({c.card_id for c in cards}) == len(cards)
    assert len({m.merchant_id for m in merchants}) == len(merchants)


def test_reproducible_with_seed():
    a = _small_world(seed=11)[5]
    b = _small_world(seed=11)[5]
    assert [t["transaction_id"] for t in a] == [t["transaction_id"] for t in b]


def test_fraud_is_injected_and_rare():
    _, _, _, _, _, txns, n_comp = _small_world()
    fraud = [t for t in txns if t["is_fraud"]]
    assert n_comp > 0 and len(fraud) > 0
    assert len(fraud) / len(txns) < 0.05          # plausibly rare
    assert all(t["fraud_type"] for t in fraud)     # every fraud is typed


def test_amounts_and_coords_valid():
    _, _, _, _, _, txns, _ = _small_world()
    for t in txns:
        assert t["amount"] > 0
        assert -90 <= t["lat"] <= 90 or abs(t["lat"]) < 200  # jitter can exceed metro
        assert t["entry_mode"] in {"chip", "contactless", "swipe", "online"}


def test_chargebacks_reference_real_fraud_and_lag():
    cfg, rng, _, _, _, txns, _ = _small_world()
    cbs = build_chargebacks(txns, cfg, rng)
    ids = {t["transaction_id"]: t for t in txns}
    assert cbs, "expected at least one chargeback"
    for cb in cbs:
        assert cb["transaction_id"] in ids
        # chargeback settles after the transaction occurs
        assert cb["settle_ts"] > ids[cb["transaction_id"]]["ts"]


def test_auth_events_cover_every_transaction():
    cfg, rng, _, _, _, txns, _ = _small_world()
    auth = build_auth_events(txns, cfg, rng)
    assert len(auth) == len(txns)


def test_haversine_known_distance():
    # London -> Paris is ~344 km
    assert 330 < haversine_km(51.5074, -0.1278, 48.8566, 2.3522) < 360
