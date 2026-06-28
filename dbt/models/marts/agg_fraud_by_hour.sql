-- Fraud KPIs by day + hour for the ops/risk dashboard.
with f as (
    select * from {{ ref('fct_transactions') }}
)

select
    event_date,
    event_hour,
    count(*)                                          as txn_count,
    sum(amount_usd)                                   as gmv_usd,
    sum(case when is_fraud_label then 1 else 0 end)   as fraud_count,
    sum(case when is_fraud_label then amount_usd else 0 end) as fraud_usd,
    sum(case when is_confirmed_fraud then amount_usd else 0 end) as chargeback_usd,
    sum(case when not is_approved then 1 else 0 end)  as declined_count,
    round(100.0 * sum(case when is_fraud_label then 1 else 0 end) / count(*), 4)
                                                      as fraud_rate_pct
from f
group by event_date, event_hour
