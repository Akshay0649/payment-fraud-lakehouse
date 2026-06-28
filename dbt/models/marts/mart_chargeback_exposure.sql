-- Finance view: confirmed chargeback losses and outstanding exposure by day.
-- "Exposure" = fraud we believe happened (label) but that has not yet charged
-- back — the dollars still at risk of becoming a loss.
with f as (
    select * from {{ ref('fct_transactions') }}
)

select
    event_date,
    sum(case when is_confirmed_fraud then amount_usd else 0 end) as confirmed_loss_usd,
    sum(case when is_fraud_label and not is_confirmed_fraud then amount_usd else 0 end)
                                                                 as outstanding_exposure_usd,
    sum(case when not is_fraud_label and is_confirmed_fraud then amount_usd else 0 end)
                                                                 as disputed_non_fraud_usd,
    count(case when is_confirmed_fraud then 1 end)               as chargeback_count
from f
group by event_date
order by event_date
