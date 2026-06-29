"""Build the portfolio page: a self-contained docs/index.html with inline SVG
charts rendered from the real KPIs (analytics/kpis.py). No JS, no CDNs - it
renders anywhere, including GitHub Pages.

Run:
    python -m ingestion.generator.generate     # if needed
    python report.py
"""
from __future__ import annotations

import os
import sys

from analytics.kpis import compute, load

REPO = "https://github.com/Akshay0649/payment-fraud-lakehouse"
OUT_HTML = "docs/index.html"

# palette
BG, CARD, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, MUTED = "#e6edf3", "#8b949e"
ACCENT, FRAUD, VOL = "#2dd4bf", "#f87171", "#388bfd"


def fmt(n) -> str:
    return f"{n:,.0f}" if isinstance(n, (int, float)) else str(n)


# ---------------------------------------------------------------- SVG charts --
def svg_hourly(hourly: list[dict]) -> str:
    W, H, l, r, t, b = 980, 240, 44, 44, 22, 28
    cw, ch = W - l - r, H - t - b
    mx_txn = max(x["txns"] for x in hourly) or 1
    mx_rate = max(x["fraud_rate_pct"] for x in hourly) or 1
    bw = cw / 24 * 0.66
    bars, pts, dots = [], [], []
    for i, x in enumerate(hourly):
        cx = l + cw * (i + 0.5) / 24
        bh = ch * x["txns"] / mx_txn
        bars.append(f'<rect x="{cx-bw/2:.1f}" y="{t+ch-bh:.1f}" width="{bw:.1f}" '
                    f'height="{bh:.1f}" rx="2" fill="{VOL}" opacity="0.35"/>')
        py = t + ch - ch * x["fraud_rate_pct"] / mx_rate
        pts.append(f"{cx:.1f},{py:.1f}")
        dots.append(f'<circle cx="{cx:.1f}" cy="{py:.1f}" r="2.4" fill="{FRAUD}"/>')
    xlab = "".join(
        f'<text x="{l+cw*(hh+0.5)/24:.1f}" y="{H-8}" fill="{MUTED}" font-size="11" '
        f'text-anchor="middle">{hh:02d}</text>' for hh in (0, 4, 8, 12, 16, 20, 23))
    return f'''<svg viewBox="0 0 {W} {H}" width="100%" role="img">
  <text x="{l}" y="14" fill="{VOL}" font-size="11">&#9632; transaction volume</text>
  <text x="{l+170}" y="14" fill="{FRAUD}" font-size="11">&#9679; fraud rate %</text>
  {''.join(bars)}
  <polyline points="{' '.join(pts)}" fill="none" stroke="{FRAUD}" stroke-width="2"/>
  {''.join(dots)}{xlab}
</svg>'''


def svg_bars(rows: list[tuple[str, float, str]], color: str, h_each=30) -> str:
    """Horizontal bar list: rows = [(label, value, right_label), ...]."""
    W, lw, rw = 460, 150, 70
    bw = W - lw - rw
    mx = max(v for _, v, _ in rows) or 1
    H = len(rows) * h_each + 8
    out = []
    for i, (label, val, rlab) in enumerate(rows):
        y = i * h_each + 6
        ln = bw * val / mx
        out.append(
            f'<text x="0" y="{y+13}" fill="{TEXT}" font-size="12">{label}</text>'
            f'<rect x="{lw}" y="{y+3}" width="{ln:.1f}" height="14" rx="3" fill="{color}"/>'
            f'<text x="{W}" y="{y+13}" fill="{MUTED}" font-size="11" text-anchor="end">{rlab}</text>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%" role="img">{"".join(out)}</svg>'


def svg_separation(sep: dict) -> str:
    nice = {"amount": "amount ($)", "geo_from_home_km": "dist. from home (km)",
            "geo_from_prev_km": "dist. from prev (km)",
            "implied_speed_kmh": "implied speed (km/h)",
            "is_new_device_pct": "new device (%)"}
    W, lw, rw, h_each = 460, 150, 120, 46
    bw = W - lw - rw
    H = len(sep["features"]) * h_each + 6
    out = []
    for i, f in enumerate(sep["features"]):
        lv, fv = sep["legit"].get(f, 0), sep["fraud"].get(f, 0)
        mx = max(lv, fv) or 1
        y = i * h_each + 4
        ll, fl = bw * lv / mx, bw * fv / mx
        out.append(
            f'<text x="0" y="{y+12}" fill="{TEXT}" font-size="12">{nice.get(f, f)}</text>'
            f'<rect x="{lw}" y="{y+4}" width="{ll:.1f}" height="11" rx="2" fill="{ACCENT}"/>'
            f'<text x="{lw+ll+5:.1f}" y="{y+13}" fill="{MUTED}" font-size="10">{lv:,.1f}</text>'
            f'<rect x="{lw}" y="{y+20}" width="{fl:.1f}" height="11" rx="2" fill="{FRAUD}"/>'
            f'<text x="{lw+fl+5:.1f}" y="{y+29}" fill="{MUTED}" font-size="10">{fv:,.1f}</text>')
    legend = (f'<text x="0" y="{H-2}" fill="{ACCENT}" font-size="11">&#9632; legit</text>'
              f'<text x="70" y="{H-2}" fill="{FRAUD}" font-size="11">&#9632; fraud</text>')
    return f'<svg viewBox="0 0 {W} {H+14}" width="100%" role="img">{"".join(out)}{legend}</svg>'


def svg_architecture() -> str:
    def box(x, y, w, label, sub, color):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="50" rx="8" fill="{CARD}" '
                f'stroke="{color}" stroke-width="1.5"/>'
                f'<text x="{x+w/2}" y="{y+21}" fill="{TEXT}" font-size="13" font-weight="600" '
                f'text-anchor="middle">{label}</text>'
                f'<text x="{x+w/2}" y="{y+38}" fill="{MUTED}" font-size="10.5" '
                f'text-anchor="middle">{sub}</text>')

    def arrow(x1, x2, y):
        return (f'<line x1="{x1}" y1="{y}" x2="{x2-7}" y2="{y}" stroke="{MUTED}" stroke-width="1.5"/>'
                f'<path d="M{x2-7},{y-4} L{x2},{y} L{x2-7},{y+4} Z" fill="{MUTED}"/>')

    return f'''<svg viewBox="0 0 980 210" width="100%" role="img">
  {box(8,12,150,"Generator","synthetic + fraud",ACCENT)}
  {box(196,12,150,"Auto Loader","Bronze Delta",VOL)}
  {box(384,12,150,"dbt Silver","features + SCD2",VOL)}
  {box(572,12,150,"dbt Gold","facts + marts",VOL)}
  {box(760,12,150,"Databricks SQL","dashboards",ACCENT)}
  {arrow(158,196,37)}{arrow(346,384,37)}{arrow(534,572,37)}{arrow(722,760,37)}
  {box(196,90,150,"Streaming","Bronze readStream",FRAUD)}
  {box(384,90,150,"Feature calc","foreachBatch",FRAUD)}
  {box(572,90,150,"MLflow model","champion score",FRAUD)}
  {box(760,90,150,"live_fraud_alerts","MERGE upsert",FRAUD)}
  {arrow(346,384,115)}{arrow(534,572,115)}{arrow(722,760,115)}
  <line x1="271" y1="62" x2="271" y2="90" stroke="{MUTED}" stroke-width="1.5"/>
  <path d="M267,83 L271,90 L275,83 Z" fill="{MUTED}"/>
  {box(384,158,338,"Chargebacks settle weeks later -> weekly retrain (champion-gated)","",MUTED)}
  <line x1="648" y1="140" x2="553" y2="158" stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="3"/>
</svg>'''


# --------------------------------------------------------------------- page ---
def kpi_card(value: str, label: str, accent=ACCENT) -> str:
    return (f'<div class="kpi"><div class="kpi-v" style="color:{accent}">{value}</div>'
            f'<div class="kpi-l">{label}</div></div>')


def build(k: dict) -> str:
    h, e = k["headline"], k["exposure"]
    kpis = "".join([
        kpi_card(f"{h['transactions']:,}", "transactions processed"),
        kpi_card(f"{h['fraud_rate_pct']}%", "fraud rate (realistic)", FRAUD),
        kpi_card(f"${h['fraud_usd']:,}", "fraud volume detected", FRAUD),
        kpi_card(f"{h['compromised_cards']:,}", "compromised cards"),
        kpi_card(f"${e['confirmed_loss_usd']:,}", "confirmed chargeback loss"),
        kpi_card(f"{h['chargebacks']:,}", "chargebacks (late labels)"),
    ])
    pat_rows = [(p["fraud_type"].replace("_", " "), p["count"],
                 f"{p['count']:,} | ${p['usd']:,.0f}") for p in k["patterns"]]
    mrows = "".join(
        f'<tr><td>{m["name"]}</td><td>{m["category"]}</td><td>{m["txns"]:,}</td>'
        f'<td>{m["fraud"]}</td><td>{m["observed_fraud_rate"]:.3f}</td>'
        f'<td>{m["baseline_fraud_rate"]:.3f}</td></tr>' for m in k["merchants"])
    stack = ["Databricks", "Delta Lake", "Auto Loader", "Unity Catalog", "dbt",
             "Structured Streaming", "MLflow", "XGBoost", "Python", "GitHub Actions"]
    badges = "".join(f'<span class="badge">{s}</span>' for s in stack)

    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Real-Time Payment Fraud Lakehouse - Akshay Raviralla</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{BG}; color:{TEXT}; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; line-height:1.55; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:0 22px 64px; }}
  a {{ color:{ACCENT}; text-decoration:none; }}
  .hero {{ padding:64px 0 28px; border-bottom:1px solid {BORDER}; }}
  .eyebrow {{ color:{ACCENT}; font-size:13px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; }}
  h1 {{ font-size:40px; line-height:1.12; margin:10px 0 12px; }}
  .lede {{ color:{MUTED}; font-size:18px; max-width:760px; }}
  .btns {{ margin-top:22px; display:flex; gap:12px; flex-wrap:wrap; }}
  .btn {{ background:{ACCENT}; color:#04221d; padding:10px 18px; border-radius:8px; font-weight:600; }}
  .btn.ghost {{ background:transparent; color:{TEXT}; border:1px solid {BORDER}; }}
  .badges {{ margin-top:24px; display:flex; gap:8px; flex-wrap:wrap; }}
  .badge {{ background:{CARD}; border:1px solid {BORDER}; color:{MUTED}; font-size:12px; padding:4px 10px; border-radius:20px; }}
  h2 {{ font-size:13px; letter-spacing:.1em; text-transform:uppercase; color:{MUTED}; margin:48px 0 16px; font-weight:700; }}
  .kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
  .kpi {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:18px; }}
  .kpi-v {{ font-size:26px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .kpi-l {{ color:{MUTED}; font-size:13px; margin-top:4px; }}
  .panel {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:20px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .panel h3 {{ margin:0 0 4px; font-size:15px; }}
  .panel p {{ color:{MUTED}; font-size:12.5px; margin:0 0 14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th,td {{ text-align:left; padding:7px 8px; border-bottom:1px solid {BORDER}; }}
  th {{ color:{MUTED}; font-weight:600; }}
  td {{ font-variant-numeric:tabular-nums; }}
  .note {{ color:{MUTED}; font-size:13px; }}
  .cardgrid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
  .feat {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:16px; }}
  .feat b {{ color:{TEXT}; }} .feat span {{ color:{MUTED}; font-size:13px; }}
  footer {{ margin-top:54px; padding-top:20px; border-top:1px solid {BORDER}; color:{MUTED}; font-size:13px; }}
  @media(max-width:720px){{ .kpis,.grid2,.cardgrid{{grid-template-columns:1fr;}} h1{{font-size:30px;}} }}
</style></head>
<body><div class="wrap">

  <div class="hero">
    <div class="eyebrow">Data Engineering &middot; Lakehouse &middot; MLOps</div>
    <h1>Real-Time Payment Fraud Analytics Platform</h1>
    <p class="lede">A production-grade, end-to-end platform that turns a raw stream of card
      transactions into real-time fraud scores and batch risk analytics - on the Databricks
      Lakehouse, modelled with dbt, scored with MLflow.</p>
    <div class="btns">
      <a class="btn" href="{REPO}">View on GitHub</a>
      <a class="btn ghost" href="{REPO}/blob/master/docs/architecture.md">Architecture</a>
      <a class="btn ghost" href="{REPO}/blob/master/README.md">README</a>
    </div>
    <div class="badges">{badges}</div>
  </div>

  <h2>The platform at a glance</h2>
  <div class="kpis">{kpis}</div>
  <p class="note" style="margin-top:12px">Numbers are computed from a reproducible
    {h['transactions']:,}-transaction synthetic dataset by the same logic as the Gold dbt
    marts (<code>analytics/kpis.py</code>). Regenerate the page with <code>python report.py</code>.</p>

  <h2>Architecture - one Delta layer, two latencies</h2>
  <div class="panel">{svg_architecture()}</div>

  <h2>Fraud signal over time</h2>
  <div class="panel">
    <h3>Fraud rate by hour of day</h3>
    <p>Fraud is injected uniformly across time; legit traffic follows a diurnal curve - so
      fraud rate spikes in the low-volume overnight hours. Emergent, not hard-coded.</p>
    {svg_hourly(k['hourly'])}
  </div>

  <div class="grid2" style="margin-top:16px">
    <div class="panel">
      <h3>Fraud by attack pattern</h3>
      <p>Five injected patterns. Card-testing is high-count/low-value; account takeover is
        the opposite - just like real fraud.</p>
      {svg_bars(pat_rows, FRAUD)}
    </div>
    <div class="panel">
      <h3>Feature separation: legit vs fraud</h3>
      <p>Per-feature averages. Clear gaps = a model can learn this. This is what the
        XGBoost scorer trains on.</p>
      {svg_separation(k['separation'])}
    </div>
  </div>

  <h2>Riskiest merchants <span style="text-transform:none;color:{MUTED};font-weight:400">~ agg_merchant_risk</span></h2>
  <div class="panel"><table>
    <tr><th>Merchant</th><th>Category</th><th>Txns</th><th>Fraud</th><th>Observed rate</th><th>Baseline</th></tr>
    {mrows}
  </table></div>

  <h2>What makes it production-grade</h2>
  <div class="cardgrid">
    <div class="feat"><b>Honest ML</b><br><span>Ground-truth labels live in Bronze for
      evaluation only - never a feature. Chargebacks (weeks late) are the production label.</span></div>
    <div class="feat"><b>Two latencies, one truth</b><br><span>Batch (dbt) and streaming (Spark)
      read/write the same Delta tables with identical feature logic, so scores agree.</span></div>
    <div class="feat"><b>Class imbalance done right</b><br><span>~0.2% positives: optimise PR-AUC,
      weight the positive class, F1-tuned alert threshold - never "accuracy".</span></div>
    <div class="feat"><b>Idempotent &amp; replayable</b><br><span>Auto Loader checkpoints dedupe
      ingestion; the streaming sink MERGEs, so re-processing updates not duplicates.</span></div>
    <div class="feat"><b>Tested data</b><br><span>40+ dbt tests incl. singular cross-model
      invariants + Python invariants, all gated in GitHub Actions CI.</span></div>
    <div class="feat"><b>Governed &amp; orchestrated</b><br><span>Unity Catalog lineage &amp;
      grants; Databricks Asset Bundles deploy batch, streaming &amp; retrain jobs.</span></div>
  </div>

  <footer>
    Generated {k['generated_at']} from a reproducible synthetic dataset (seed-stable).
    &middot; <a href="{REPO}">Akshay0649/payment-fraud-lakehouse</a>
  </footer>
</div></body></html>'''


def main() -> None:
    try:
        k = compute(load())
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build(k))
    print(f"[report] wrote {OUT_HTML} "
          f"({k['headline']['transactions']:,} txns, {k['headline']['fraud_rate_pct']}% fraud)")


if __name__ == "__main__":
    main()
