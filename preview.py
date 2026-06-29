"""Local analytics preview - see the project's outcome WITHOUT Databricks.

Computes the same KPIs as the Gold dbt marts (via analytics/kpis.py) from the
generator's JSON output and prints them as terminal tables. A fast sanity check
that the warehouse logic is sound before deploying to Databricks.

Run:
    python -m ingestion.generator.generate     # if you haven't generated data yet
    python preview.py
"""
from __future__ import annotations

import sys

from analytics.kpis import compute, load


def _rule(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def main() -> None:
    try:
        k = compute(load())
    except FileNotFoundError as e:
        sys.exit(str(e))

    h = k["headline"]
    _rule("HEADLINE  -  overall fraud KPIs")
    print(f"  transactions ....... {h['transactions']:>12,}")
    print(f"  labelled fraud ..... {h['fraud_count']:>12,}  ({h['fraud_rate_pct']}% of txns)")
    print(f"  gross volume ....... ${h['gmv_usd']:>12,}")
    print(f"  fraud volume ....... ${h['fraud_usd']:>12,}")
    print(f"  chargebacks ........ {h['chargebacks']:>12,}")
    print(f"  compromised cards .. {h['compromised_cards']:>12,}")

    _rule("FRAUD BY PATTERN  -  what kind of fraud was injected")
    print(f"  {'pattern':<20}{'count':>8}{'usd':>12}")
    for p in k["patterns"]:
        print(f"  {p['fraud_type']:<20}{p['count']:>8,}{p['usd']:>12,.0f}")

    _rule("FRAUD RATE BY HOUR OF DAY  ~ agg_fraud_by_hour")
    mx = max(r["txns"] for r in k["hourly"]) or 1
    print(f"  {'hr':>3}{'txns':>8}{'fraud':>7}{'rate%':>8}  volume")
    for r in k["hourly"]:
        bar = "#" * int(28 * r["txns"] / mx)
        print(f"  {r['event_hour']:>3}{r['txns']:>8,}{r['fraud']:>7}{r['fraud_rate_pct']:>8} {bar}")

    _rule("TOP RISKIEST MERCHANTS  ~ agg_merchant_risk")
    print(f"  {'merchant':<22}{'category':<16}{'txns':>6}{'fraud':>6}{'observed':>10}{'baseline':>10}")
    for r in k["merchants"]:
        print(f"  {r['name']:<22}{r['category']:<16}{r['txns']:>6}{r['fraud']:>6}"
              f"{r['observed_fraud_rate']:>10.4f}{r['baseline_fraud_rate']:>10.4f}")

    e = k["exposure"]
    _rule("CHARGEBACK EXPOSURE  ~ mart_chargeback_exposure")
    print(f"  confirmed loss (charged back) ...... ${e['confirmed_loss_usd']:>11,}")
    print(f"  outstanding exposure (fraud, no CB)  ${e['outstanding_usd']:>11,}")
    print(f"  disputed non-fraud (label noise) ... ${e['disputed_non_fraud_usd']:>11,}")

    s = k["separation"]
    _rule("FEATURE SEPARATION  -  legit vs fraud  ~ int_transactions_enriched")
    print(f"  {'feature':<22}{'legit':>14}{'fraud':>14}")
    for f in s["features"]:
        print(f"  {f:<22}{s['legit'].get(f, 0):>14,.2f}{s['fraud'].get(f, 0):>14,.2f}")
    print("\n  Every feature separates the classes - this is what ml/train.py learns,")
    print("  and why PR-AUC (not accuracy) is the metric that matters at 0.2% fraud.")

    print(f"\n{'-' * 78}")
    print("  These mirror the Gold marts. On Databricks the same numbers render as a")
    print("  Databricks SQL dashboard (see dashboards/queries.sql) or the portfolio")
    print("  page (run: python report.py -> docs/index.html).")
    print(f"{'-' * 78}\n")


if __name__ == "__main__":
    main()
