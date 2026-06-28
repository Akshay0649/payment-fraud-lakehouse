-- Model performance over time: how well do the live fraud scores match reality?
-- Reality = the ground-truth label (and, in production, confirmed chargebacks).
-- Depends on databricks/streaming_score.py having populated gold.live_fraud_alerts.
{{ config(materialized='table') }}

with scores as (
    select transaction_id, fraud_score, is_alert
    from {{ source('gold', 'live_fraud_alerts') }}
),

labels as (
    select transaction_id, event_date, is_fraud_label, is_confirmed_fraud
    from {{ ref('fct_transactions') }}
),

joined as (
    select
        l.event_date,
        l.is_fraud_label,
        s.is_alert,
        s.fraud_score
    from labels l
    join scores s on l.transaction_id = s.transaction_id
)

select
    event_date,
    count(*)                                                          as scored_txns,
    sum(case when is_alert and is_fraud_label then 1 else 0 end)      as true_positives,
    sum(case when is_alert and not is_fraud_label then 1 else 0 end)  as false_positives,
    sum(case when not is_alert and is_fraud_label then 1 else 0 end)  as false_negatives,
    round(
        sum(case when is_alert and is_fraud_label then 1 else 0 end)
        / nullif(sum(case when is_alert then 1 else 0 end), 0), 4)    as precision,
    round(
        sum(case when is_alert and is_fraud_label then 1 else 0 end)
        / nullif(sum(case when is_fraud_label then 1 else 0 end), 0), 4) as recall
from joined
group by event_date
order by event_date
