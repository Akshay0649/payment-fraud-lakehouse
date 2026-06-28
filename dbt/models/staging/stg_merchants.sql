with source as (
    select * from {{ source('bronze', 'raw_merchants') }}
)

select
    merchant_id,
    name                            as merchant_name,
    category                        as merchant_category,
    country                         as merchant_country,
    cast(lat as double)             as lat,
    cast(lon as double)             as lon,
    cast(baseline_fraud_rate as double) as baseline_fraud_rate
from source
