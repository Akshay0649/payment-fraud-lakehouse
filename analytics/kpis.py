"""Compute the Gold-mart KPIs from the generator's JSON output.

This is the single source of truth for the numbers shown in both `preview.py`
(console) and `report.py` (HTML portfolio page). Each function mirrors one Gold
dbt model so the local artifacts and the Databricks warehouse agree.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd


def _load_jsonl(out: str, subdir: str) -> pd.DataFrame:
    files = glob.glob(os.path.join(out, subdir, "**", "*.jsonl"), recursive=True)
    rows = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f)
    return pd.DataFrame(rows)


def _load_ref(out: str, name: str) -> pd.DataFrame:
    with open(os.path.join(out, "reference", f"{name}.json"), encoding="utf-8") as f:
        return pd.DataFrame(json.load(f))


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def load(out: str = "ingestion/output") -> dict:
    if not os.path.isdir(out):
        raise FileNotFoundError(
            f"No data at {out}. Run: python -m ingestion.generator.generate")
    txn = _load_jsonl(out, "transactions")
    cb = _load_jsonl(out, "chargebacks")
    cards = _load_ref(out, "cards")
    merchants = _load_ref(out, "merchants")

    txn["event_ts"] = pd.to_datetime(txn["ts"], format="ISO8601")
    txn["event_hour"] = txn["event_ts"].dt.hour
    cb_ids = set(cb["transaction_id"]) if not cb.empty else set()
    txn["is_confirmed_fraud"] = txn["transaction_id"].isin(cb_ids)
    return {"txn": txn, "cb": cb, "cards": cards, "merchants": merchants}


def compute(data: dict) -> dict:
    """Return all KPIs as plain JSON-serialisable structures."""
    txn, cb = data["txn"], data["cb"]
    cards, merchants = data["cards"], data["merchants"]
    n = len(txn)
    fraud_mask = txn["is_fraud"]

    headline = {
        "transactions": int(n),
        "fraud_count": int(fraud_mask.sum()),
        "fraud_rate_pct": round(fraud_mask.sum() / n * 100, 3),
        "gmv_usd": round(float(txn["amount"].sum())),
        "fraud_usd": round(float(txn.loc[fraud_mask, "amount"].sum())),
        "chargebacks": int(len(cb)),
        "compromised_cards": int(txn.loc[fraud_mask, "card_id"].nunique()),
        "window_days": int(txn["event_ts"].dt.normalize().nunique()),
    }

    patterns = (txn[fraud_mask].groupby("fraud_type")
                .agg(count=("transaction_id", "size"), usd=("amount", "sum"))
                .sort_values("count", ascending=False).reset_index())
    patterns["usd"] = patterns["usd"].round(0)
    patterns = patterns.to_dict("records")

    hourly = (txn.groupby("event_hour")
              .agg(txns=("transaction_id", "size"), fraud=("is_fraud", "sum"))
              .reindex(range(24), fill_value=0).reset_index())
    hourly["fraud_rate_pct"] = (100 * hourly["fraud"] / hourly["txns"].replace(0, np.nan)).round(3).fillna(0)
    hourly = hourly.to_dict("records")

    m = (txn.groupby("merchant_id")
         .agg(txns=("transaction_id", "size"), fraud=("is_fraud", "sum"),
              gmv=("amount", "sum")).reset_index()
         .merge(merchants[["merchant_id", "name", "category", "baseline_fraud_rate"]],
                on="merchant_id", how="left"))
    m = m[m["txns"] >= 20].copy()
    m["observed_fraud_rate"] = (m["fraud"] / m["txns"]).round(4)
    merchants_top = (m.sort_values("observed_fraud_rate", ascending=False)
                     .head(12)[["name", "category", "txns", "fraud",
                                "observed_fraud_rate", "baseline_fraud_rate"]]
                     .to_dict("records"))

    exposure = {
        "confirmed_loss_usd": round(float(txn.loc[txn["is_confirmed_fraud"], "amount"].sum())),
        "outstanding_usd": round(float(txn.loc[fraud_mask & ~txn["is_confirmed_fraud"], "amount"].sum())),
        "disputed_non_fraud_usd": round(float(txn.loc[~fraud_mask & txn["is_confirmed_fraud"], "amount"].sum())),
    }

    separation = _feature_separation(txn, cards)

    return {
        "headline": headline,
        "patterns": patterns,
        "hourly": hourly,
        "merchants": merchants_top,
        "exposure": exposure,
        "separation": separation,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _feature_separation(txn: pd.DataFrame, cards: pd.DataFrame) -> dict:
    df = txn.merge(cards[["card_id", "home_lat", "home_lon", "primary_device_id"]],
                   on="card_id", how="left").sort_values(["card_id", "event_ts"])
    g = df.groupby("card_id")
    df["prev_ts"] = g["event_ts"].shift()
    df["prev_lat"] = g["lat"].shift()
    df["prev_lon"] = g["lon"].shift()
    df["seconds_since_prev"] = (df["event_ts"] - df["prev_ts"]).dt.total_seconds()
    df["geo_from_home_km"] = _haversine_km(df["lat"], df["lon"], df["home_lat"], df["home_lon"])
    df["geo_from_prev_km"] = _haversine_km(df["lat"], df["lon"], df["prev_lat"], df["prev_lon"]).fillna(0)
    df["implied_speed_kmh"] = np.where(
        df["seconds_since_prev"].fillna(0) > 0,
        df["geo_from_prev_km"] / (df["seconds_since_prev"] / 3600.0), 0)
    df["is_new_device_pct"] = (df["device_id"] != df["primary_device_id"]).astype(int) * 100
    feats = ["amount", "geo_from_home_km", "geo_from_prev_km",
             "implied_speed_kmh", "is_new_device_pct"]
    sep = df.groupby("is_fraud")[feats].mean().round(2)
    return {
        "features": feats,
        "legit": sep.loc[False].to_dict() if False in sep.index else {},
        "fraud": sep.loc[True].to_dict() if True in sep.index else {},
    }
