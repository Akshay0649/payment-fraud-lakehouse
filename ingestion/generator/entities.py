"""Reference / dimension entities: accounts, cards, merchants.

These are the slowly-changing dimensions of the warehouse. They are emitted once
as reference files; the transaction stream references them by id.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .config import GeneratorConfig

# A handful of metro anchor points (lat, lon) so that geo-distance features are
# meaningful and "impossible travel" is actually impossible.
METROS: list[tuple[str, float, float]] = [
    ("GB", 51.5074, -0.1278),    # London
    ("US", 40.7128, -74.0060),   # New York
    ("US", 37.7749, -122.4194),  # San Francisco
    ("DE", 52.5200, 13.4050),    # Berlin
    ("IN", 19.0760, 72.8777),    # Mumbai
    ("SG", 1.3521, 103.8198),    # Singapore
    ("BR", -23.5505, -46.6333),  # Sao Paulo
]


def _jitter(lat: float, lon: float, km: float, rng: random.Random) -> tuple[float, float]:
    """Offset a point by up to `km` kilometres in a random direction."""
    bearing = rng.uniform(0, 2 * math.pi)
    dist_deg = (km / 111.0) * math.sqrt(rng.random())
    return lat + dist_deg * math.cos(bearing), lon + dist_deg * math.sin(bearing)


@dataclass
class Merchant:
    merchant_id: str
    name: str
    category: str
    country: str
    lat: float
    lon: float
    baseline_fraud_rate: float


@dataclass
class Card:
    card_id: str
    account_id: str
    card_type: str
    issue_date: str
    primary_device_id: str
    home_lat: float
    home_lon: float
    home_country: str
    # mutable runtime state (not serialised to the card dimension)
    is_active: bool = True
    last_ts: float | None = field(default=None, repr=False)
    last_lat: float | None = field(default=None, repr=False)
    last_lon: float | None = field(default=None, repr=False)
    amount_sum: float = field(default=0.0, repr=False)
    amount_sqsum: float = field(default=0.0, repr=False)
    amount_n: int = field(default=0, repr=False)
    known_devices: set[str] = field(default_factory=set, repr=False)

    def observe_amount(self, amount: float) -> None:
        self.amount_sum += amount
        self.amount_sqsum += amount * amount
        self.amount_n += 1

    def amount_stats(self) -> tuple[float, float]:
        """Return (mean, std) of the card's historical legit amounts."""
        if self.amount_n < 2:
            return 50.0, 50.0  # weak prior until we have history
        mean = self.amount_sum / self.amount_n
        var = max(self.amount_sqsum / self.amount_n - mean * mean, 1.0)
        return mean, math.sqrt(var)


@dataclass
class Account:
    account_id: str
    signup_date: str
    home_country: str
    risk_band: str


def build_world(cfg: GeneratorConfig, rng: random.Random):
    """Construct accounts, cards and merchants. Returns (accounts, cards, merchants)."""
    accounts: list[Account] = []
    cards: list[Card] = []
    merchants: list[Merchant] = []

    # --- merchants -----------------------------------------------------------
    for i in range(cfg.n_merchants):
        country, lat, lon = rng.choice(METROS)
        mlat, mlon = _jitter(lat, lon, 30, rng)
        category = rng.choice(cfg.merchant_categories)
        base = 0.06 if category in cfg.high_risk_categories else 0.004
        merchants.append(Merchant(
            merchant_id=f"M{i:05d}",
            name=f"{category.title().replace('_', ' ')} #{i:04d}",
            category=category,
            country=country,
            lat=round(mlat, 5),
            lon=round(mlon, 5),
            baseline_fraud_rate=round(base * rng.uniform(0.5, 1.8), 4),
        ))

    # --- accounts + cards ----------------------------------------------------
    risk_bands = ["low", "low", "low", "medium", "high"]  # skew toward low
    for i in range(cfg.n_accounts):
        country, lat, lon = rng.choice(METROS)
        signup = cfg.end_ts.timestamp() - rng.uniform(180, 1500) * 86400
        acct = Account(
            account_id=f"A{i:06d}",
            signup_date=_iso_date(signup),
            home_country=country,
            risk_band=rng.choice(risk_bands),
        )
        accounts.append(acct)

        n_cards = rng.randint(*cfg.cards_per_account)
        for c in range(n_cards):
            hlat, hlon = _jitter(lat, lon, 15, rng)
            device = f"D{rng.getrandbits(48):012x}"
            card = Card(
                card_id=f"C{i:06d}{c}",
                account_id=acct.account_id,
                card_type=rng.choice(["credit", "debit"]),
                issue_date=_iso_date(signup + rng.uniform(0, 60) * 86400),
                primary_device_id=device,
                home_lat=round(hlat, 5),
                home_lon=round(hlon, 5),
                home_country=country,
            )
            card.known_devices.add(device)
            cards.append(card)

    return accounts, cards, merchants


def _iso_date(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
