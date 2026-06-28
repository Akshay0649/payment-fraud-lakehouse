with source as (
    select * from {{ source('bronze', 'raw_cards') }}
)

select
    card_id,
    account_id,
    card_type,
    cast(issue_date as date)    as issue_date,
    primary_device_id,
    cast(home_lat as double)    as home_lat,
    cast(home_lon as double)    as home_lon,
    home_country,
    cast(is_active as boolean)  as is_active
from source
