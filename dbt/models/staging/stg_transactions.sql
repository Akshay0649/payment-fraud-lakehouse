-- 1:1 cleanup of raw transactions: cast types, standardise names. No joins,
-- no business logic — that belongs in intermediate/marts.
with source as (
    select * from {{ source('bronze', 'raw_transactions') }}
)

select
    transaction_id,
    card_id,
    merchant_id,
    cast(amount as double)              as amount_usd,
    currency,
    cast(ts as timestamp)               as event_ts,
    cast(event_date as date)            as event_date,
    cast(lat as double)                 as lat,
    cast(lon as double)                 as lon,
    device_id,
    entry_mode,
    -- ground-truth label: kept for evaluation only, never used as a feature.
    cast(is_fraud as boolean)           as is_fraud_label,
    fraud_type                          as fraud_type_label
from source
