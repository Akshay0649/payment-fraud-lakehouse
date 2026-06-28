"""Fraud-pattern injectors.

Design principle: each injector produces transactions whose *parameters*
(timing, geo, device, amount, merchant) naturally exhibit the signal a real
fraud model would learn — we never write the label into a feature. The label
(`is_fraud=True`, plus a `fraud_type` for explainability/eval) is ground truth
that lives in Bronze and is dropped before model features are built.

Each injector returns a list of ``FraudSpec`` describing transactions to emit
on top of the card's legitimate history.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .entities import Card, Merchant, _jitter


@dataclass
class FraudSpec:
    ts: float
    amount: float
    lat: float
    lon: float
    device_id: str
    merchant: Merchant
    entry_mode: str
    fraud_type: str


def _pick(merchants: list[Merchant], rng: random.Random, category: str | None = None) -> Merchant:
    if category:
        pool = [m for m in merchants if m.category == category]
        if pool:
            return rng.choice(pool)
    return rng.choice(merchants)


def card_testing(card: Card, merchants: list[Merchant], t0: float, rng: random.Random) -> list[FraudSpec]:
    """Stolen card validated with a burst of tiny online charges in seconds.
    Signal: extreme velocity + uniformly small amounts + online entry."""
    m = _pick(merchants, rng, "online_retail")
    device = f"D{rng.getrandbits(48):012x}"  # attacker device
    out, t = [], t0
    for _ in range(rng.randint(6, 14)):
        t += rng.uniform(2, 25)  # seconds apart
        out.append(FraudSpec(
            ts=t, amount=round(rng.uniform(0.5, 3.0), 2),
            lat=card.home_lat, lon=card.home_lon, device_id=device,
            merchant=m, entry_mode="online", fraud_type="card_testing",
        ))
    return out


def account_takeover(card: Card, merchants: list[Merchant], t0: float, rng: random.Random) -> list[FraudSpec]:
    """New device + new geography, then high-value purchases.
    Signal: unknown device + large geo jump + amount spike."""
    far_lat, far_lon = _jitter(card.home_lat + rng.choice([-25, 25]),
                               card.home_lon + rng.choice([-40, 40]), 50, rng)
    device = f"D{rng.getrandbits(48):012x}"
    out, t = [], t0
    for _ in range(rng.randint(1, 3)):
        t += rng.uniform(300, 3600)
        m = _pick(merchants, rng, "electronics")
        out.append(FraudSpec(
            ts=t, amount=round(rng.uniform(400, 2500), 2),
            lat=round(far_lat, 5), lon=round(far_lon, 5), device_id=device,
            merchant=m, entry_mode="online", fraud_type="account_takeover",
        ))
    return out


def geo_impossible(card: Card, merchants: list[Merchant], t0: float, rng: random.Random) -> list[FraudSpec]:
    """A charge physically impossible given the card's previous location/time.
    Signal: high geo_distance / low seconds_since_prev -> implied speed > jet."""
    far_lat, far_lon = card.home_lat + rng.choice([-30, 30]), card.home_lon + rng.choice([-50, 50])
    m = _pick(merchants, rng)
    return [FraudSpec(
        ts=t0 + rng.uniform(60, 600),  # minutes after a home-city legit txn
        amount=round(rng.uniform(50, 600), 2),
        lat=round(far_lat, 5), lon=round(far_lon, 5),
        device_id=f"D{rng.getrandbits(48):012x}",
        merchant=m, entry_mode="swipe", fraud_type="geo_impossible",
    )]


def amount_anomaly(card: Card, merchants: list[Merchant], t0: float, rng: random.Random) -> list[FraudSpec]:
    """One charge far outside the card's historical amount distribution.
    Signal: amount z-score >> typical."""
    mean, std = card.amount_stats()
    m = _pick(merchants, rng)
    return [FraudSpec(
        ts=t0 + rng.uniform(60, 7200),
        amount=round(mean + std * rng.uniform(8, 18) + 500, 2),
        lat=card.home_lat, lon=card.home_lon,
        device_id=card.primary_device_id,  # subtle: known device, anomalous amount
        merchant=m, entry_mode="contactless", fraud_type="amount_anomaly",
    )]


def merchant_collusion(card: Card, merchants: list[Merchant], t0: float, rng: random.Random) -> list[FraudSpec]:
    """Charges at a structurally high-risk merchant skimming cards.
    Signal: high mcc_risk_score + clustered timing."""
    high_risk = [m for m in merchants if m.category in
                 ("crypto_exchange", "gambling", "money_transfer")]
    m = rng.choice(high_risk) if high_risk else _pick(merchants, rng)
    out, t = [], t0
    for _ in range(rng.randint(1, 4)):
        t += rng.uniform(600, 5400)
        out.append(FraudSpec(
            ts=t, amount=round(rng.uniform(100, 900), 2),
            lat=card.home_lat, lon=card.home_lon, device_id=card.primary_device_id,
            merchant=m, entry_mode="online", fraud_type="merchant_collusion",
        ))
    return out


PATTERNS = [
    card_testing,
    account_takeover,
    geo_impossible,
    amount_anomaly,
    merchant_collusion,
]
