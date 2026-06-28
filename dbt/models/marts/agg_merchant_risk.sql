-- Per-merchant risk scorecard: actual fraud experience vs. baseline.
with f as (
    select * from {{ ref('fct_transactions') }}
),

m as (
    select * from {{ ref('dim_merchant') }}
)

select
    m.merchant_id,
    m.merchant_name,
    m.merchant_category,
    m.risk_tier,
    m.baseline_fraud_rate,
    count(*)                                              as txn_count,
    sum(f.amount_usd)                                     as gmv_usd,
    sum(case when f.is_fraud_label then 1 else 0 end)     as fraud_count,
    round(sum(case when f.is_fraud_label then 1 else 0 end) / count(*), 4)
                                                          as observed_fraud_rate,
    sum(case when f.is_confirmed_fraud then f.amount_usd else 0 end) as chargeback_usd
from f
join m on f.merchant_id = m.merchant_id
group by 1, 2, 3, 4, 5
