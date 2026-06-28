with source as (
    select * from {{ source('bronze', 'raw_chargebacks') }}
)

select
    chargeback_id,
    transaction_id,
    cast(amount as double)        as chargeback_amount,
    reason_code,
    cast(settle_ts as timestamp)  as settle_ts,
    cast(settle_date as date)     as settle_date
from source
