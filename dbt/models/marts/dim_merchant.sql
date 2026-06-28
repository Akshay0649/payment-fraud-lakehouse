-- Conformed merchant dimension with a derived risk tier used across marts.
with merchants as (
    select * from {{ ref('stg_merchants') }}
)

select
    merchant_id,
    merchant_name,
    merchant_category,
    merchant_country,
    baseline_fraud_rate,
    case
        when merchant_category in ('crypto_exchange', 'gambling', 'money_transfer') then 'high'
        when merchant_category in ('electronics', 'travel') then 'medium'
        else 'low'
    end as risk_tier
from merchants
