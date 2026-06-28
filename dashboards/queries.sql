-- Headline queries for the Databricks SQL dashboard. Each maps to one tile.
-- All read from the Gold marts produced by dbt.

-- 1) Daily fraud rate & dollars at risk (line + bar) ------------------------
select event_date,
       sum(txn_count)                              as transactions,
       round(sum(fraud_usd), 0)                    as fraud_dollars,
       round(100.0 * sum(fraud_count) / sum(txn_count), 3) as fraud_rate_pct
from fraud.gold.agg_fraud_by_hour
group by event_date
order by event_date;

-- 2) Fraud by hour-of-day heatmap -------------------------------------------
select event_hour,
       round(avg(fraud_rate_pct), 3) as avg_fraud_rate_pct,
       sum(fraud_count)              as fraud_count
from fraud.gold.agg_fraud_by_hour
group by event_hour
order by event_hour;

-- 3) Top-20 riskiest merchants (table) --------------------------------------
select merchant_name, merchant_category, risk_tier,
       txn_count, observed_fraud_rate, round(chargeback_usd, 0) as chargeback_usd
from fraud.gold.agg_merchant_risk
where txn_count > 20
order by observed_fraud_rate desc
limit 20;

-- 4) Chargeback exposure vs. confirmed loss (area) --------------------------
select event_date, confirmed_loss_usd, outstanding_exposure_usd
from fraud.gold.mart_chargeback_exposure
order by event_date;

-- 5) Model precision/recall trend (line) ------------------------------------
select event_date, precision, recall, scored_txns
from fraud.gold.mart_model_performance
order by event_date;

-- 6) Live alert queue (real-time tile, refresh 10s) -------------------------
select a.transaction_id, a.card_id, m.merchant_name, a.amount,
       round(a.fraud_score, 3) as score, a.event_ts
from fraud.gold.live_fraud_alerts a
left join fraud.gold.dim_merchant m on a.merchant_id = m.merchant_id
where a.is_alert
order by a.event_ts desc
limit 100;
