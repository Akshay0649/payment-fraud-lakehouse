with source as (
    select * from {{ source('bronze', 'raw_accounts') }}
)

select
    account_id,
    cast(signup_date as date)   as signup_date,
    home_country,
    risk_band
from source
