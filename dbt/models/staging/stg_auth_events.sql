with source as (
    select * from {{ source('bronze', 'raw_auth_events') }}
)

select
    transaction_id,
    cast(approved as boolean)   as is_approved,
    reason                      as decline_reason,
    cast(auth_ts as timestamp)  as auth_ts
from source
