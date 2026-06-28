"""Batch generator entrypoint.

Produces a reproducible payment world and writes Bronze-ready files:

    ingestion/output/
      reference/{accounts,cards,merchants}.json   # dimensions
      transactions/event_date=YYYY-MM-DD/part.jsonl
      auth_events/event_date=YYYY-MM-DD/part.jsonl
      chargebacks/settle_date=YYYY-MM-DD/part.jsonl

Run:
    python -m ingestion.generator.generate --accounts 2000 --window-days 30
"""
from __future__ import annotations

import argparse
import json
import os
import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from .config import CONFIG, GeneratorConfig
from .entities import Card, Merchant, build_world, _jitter
from .fraud_patterns import PATTERNS

# Hour-of-day weights: low overnight, peaks at lunch and evening. Drives realistic
# diurnal seasonality so `agg_fraud_by_hour` and time features are meaningful.
HOUR_WEIGHTS = [
    0.2, 0.1, 0.1, 0.1, 0.1, 0.2, 0.5, 1.0, 1.4, 1.6, 1.7, 1.9,
    2.1, 1.8, 1.6, 1.6, 1.7, 1.9, 2.2, 2.0, 1.6, 1.1, 0.7, 0.4,
]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _sample_ts_in_day(day_start: float, rng: random.Random) -> float:
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS, k=1)[0]
    return day_start + hour * 3600 + rng.uniform(0, 3600)


def _det_uuid(rng: random.Random) -> str:
    """Deterministic UUID4 from the seeded RNG so whole datasets are byte-stable."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def make_txn(card: Card, merchant: Merchant, ts: float, amount: float,
             lat: float, lon: float, device_id: str, entry_mode: str,
             is_fraud: bool, fraud_type: str | None, rng: random.Random) -> dict:
    return {
        "transaction_id": _det_uuid(rng),
        "card_id": card.card_id,
        "merchant_id": merchant.merchant_id,
        "amount": round(amount, 2),
        "currency": "USD",
        "ts": _iso(ts),
        "event_date": _date(ts),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "device_id": device_id,
        "entry_mode": entry_mode,
        # ground truth — consumed by ML training & eval, never by feature builders
        "is_fraud": is_fraud,
        "fraud_type": fraud_type,
        "_ts_epoch": ts,  # internal sort key, stripped on write
    }


def generate_legit(card: Card, merchants: list[Merchant], cfg: GeneratorConfig,
                   rng: random.Random, start_ts: float) -> list[dict]:
    out: list[dict] = []
    for d in range(cfg.window_days):
        day_start = start_ts + d * 86400
        n = _poisson(cfg.txns_per_card_per_day, rng)
        for _ in range(n):
            ts = _sample_ts_in_day(day_start, rng)
            # Legitimate cardholders transact near home: an online purchase happens
            # from the cardholder's location, a card-present one at a nearby shop.
            # (Merchant *metadata* may live in another metro; the transaction geo is
            # the cardholder's, which is what the geo features actually measure.)
            m = rng.choice(merchants)
            lat, lon = _jitter(card.home_lat, card.home_lon, 10, rng)
            mean, std = card.amount_stats()
            amount = max(1.0, rng.lognormvariate(cfg.amount_lognorm_mu, cfg.amount_lognorm_sigma))
            device = card.primary_device_id if rng.random() < 0.97 else f"D{rng.getrandbits(48):012x}"
            card.known_devices.add(device)
            card.observe_amount(amount)
            out.append(make_txn(card, m, ts, amount, lat, lon, device,
                                _entry_mode(m, rng), False, None, rng))
    return out


def _entry_mode(m: Merchant, rng: random.Random) -> str:
    if m.category in ("online_retail", "crypto_exchange", "gambling", "money_transfer"):
        return "online"
    return rng.choices(["chip", "contactless", "swipe", "online"],
                       weights=[0.45, 0.35, 0.1, 0.1], k=1)[0]


def _poisson(lam: float, rng: random.Random) -> int:
    # Knuth's algorithm — avoids a numpy dependency.
    import math
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def inject_fraud(cards: list[Card], merchants: list[Merchant], cfg: GeneratorConfig,
                 rng: random.Random, start_ts: float) -> tuple[list[dict], int]:
    fraud_txns: list[dict] = []
    n_compromised = 0
    for card in cards:
        if rng.random() >= cfg.compromised_card_rate:
            continue
        n_compromised += 1
        pattern = rng.choice(PATTERNS)
        t0 = start_ts + rng.uniform(0.1, 0.95) * cfg.window_days * 86400
        for spec in pattern(card, merchants, t0, rng):  # type: ignore[operator]
            fraud_txns.append(make_txn(
                card, spec.merchant, spec.ts, spec.amount, spec.lat, spec.lon,
                spec.device_id, spec.entry_mode, True, spec.fraud_type, rng))
    return fraud_txns, n_compromised


def build_auth_events(txns: list[dict], cfg: GeneratorConfig, rng: random.Random) -> list[dict]:
    """Authorisation decisions. Real fraud rules already catch some fraud
    (declined), and some good txns are declined by chance — both create the
    precision/recall tension the platform exists to measure."""
    events = []
    for t in txns:
        if t["is_fraud"]:
            approved = rng.random() > 0.35  # 35% of fraud blocked at auth
            reason = "approved" if approved else rng.choice(["risk_block", "velocity_block"])
        else:
            approved = rng.random() > cfg.base_decline_rate
            reason = "approved" if approved else rng.choice(["insufficient_funds", "do_not_honor"])
        events.append({
            "transaction_id": t["transaction_id"],
            "approved": approved,
            "reason": reason,
            "auth_ts": t["ts"],
            "event_date": t["event_date"],
        })
    return events


def build_chargebacks(txns: list[dict], cfg: GeneratorConfig, rng: random.Random) -> list[dict]:
    """Late-arriving production labels. Most confirmed fraud is charged back
    weeks later; a few legit txns are disputed too (label noise)."""
    cbs = []
    for t in txns:
        is_cb = False
        if t["is_fraud"] and rng.random() < cfg.chargeback_recovery_rate:
            is_cb, kind = True, "fraud"
        elif (not t["is_fraud"]) and rng.random() < cfg.false_chargeback_rate:
            is_cb, kind = True, "dispute"
        if not is_cb:
            continue
        lag = rng.randint(*cfg.chargeback_lag_days) * 86400
        settle = t["_ts_epoch"] + lag
        cbs.append({
            "chargeback_id": _det_uuid(rng),
            "transaction_id": t["transaction_id"],
            "amount": t["amount"],
            "reason_code": kind,
            "settle_ts": _iso(settle),
            "settle_date": _date(settle),
        })
    return cbs


def _write_partitioned(rows: list[dict], base: str, part_key: str, drop: tuple[str, ...] = ()) -> int:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[part_key]].append(r)
    written = 0
    for key, items in groups.items():
        d = os.path.join(base, f"{part_key}={key}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "part-000.jsonl"), "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps({k: v for k, v in it.items() if k not in drop}) + "\n")
                written += 1
    return written


def _write_json(rows: list, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def run(cfg: GeneratorConfig) -> None:
    rng = random.Random(cfg.seed)
    start_ts = cfg.end_ts.timestamp() - cfg.window_days * 86400

    print(f"[gen] building world: {cfg.n_accounts} accounts, {cfg.n_merchants} merchants")
    accounts, cards, merchants = build_world(cfg, rng)
    print(f"[gen] {len(cards)} cards")

    print("[gen] generating legitimate transactions...")
    txns: list[dict] = []
    for card in cards:
        txns.extend(generate_legit(card, merchants, cfg, rng, start_ts))

    print("[gen] injecting fraud...")
    fraud_txns, n_comp = inject_fraud(cards, merchants, cfg, rng, start_ts)
    txns.extend(fraud_txns)
    txns.sort(key=lambda t: t["_ts_epoch"])

    n_fraud = sum(1 for t in txns if t["is_fraud"])
    print(f"[gen] {len(txns):,} transactions | {n_fraud:,} fraud "
          f"({n_fraud / len(txns) * 100:.2f}%) across {n_comp} compromised cards")

    auth = build_auth_events(txns, cfg, rng)
    cbs = build_chargebacks(txns, cfg, rng)

    out = cfg.output_dir
    _write_json([a.__dict__ for a in accounts], os.path.join(out, "reference", "accounts.json"))
    _write_json([_card_dim(c) for c in cards], os.path.join(out, "reference", "cards.json"))
    _write_json([m.__dict__ for m in merchants], os.path.join(out, "reference", "merchants.json"))
    nt = _write_partitioned(txns, os.path.join(out, "transactions"), "event_date", drop=("_ts_epoch",))
    na = _write_partitioned(auth, os.path.join(out, "auth_events"), "event_date")
    nc = _write_partitioned(cbs, os.path.join(out, "chargebacks"), "settle_date")
    print(f"[gen] wrote transactions={nt:,} auth_events={na:,} chargebacks={nc:,} -> {out}/")


def _card_dim(c: Card) -> dict:
    return {
        "card_id": c.card_id, "account_id": c.account_id, "card_type": c.card_type,
        "issue_date": c.issue_date, "primary_device_id": c.primary_device_id,
        "home_lat": c.home_lat, "home_lon": c.home_lon, "home_country": c.home_country,
        "is_active": c.is_active,
    }


def parse_args() -> GeneratorConfig:
    import dataclasses
    p = argparse.ArgumentParser(description="Synthetic payment-fraud stream generator")
    p.add_argument("--accounts", type=int, default=CONFIG.n_accounts)
    p.add_argument("--merchants", type=int, default=CONFIG.n_merchants)
    p.add_argument("--window-days", type=int, default=CONFIG.window_days)
    p.add_argument("--seed", type=int, default=CONFIG.seed)
    p.add_argument("--fraud-rate", type=float, default=CONFIG.compromised_card_rate,
                   help="fraction of cards compromised")
    p.add_argument("--out", type=str, default=CONFIG.output_dir)
    a = p.parse_args()
    return dataclasses.replace(
        CONFIG, n_accounts=a.accounts, n_merchants=a.merchants, window_days=a.window_days,
        seed=a.seed, compromised_card_rate=a.fraud_rate, output_dir=a.out)


if __name__ == "__main__":
    run(parse_args())
