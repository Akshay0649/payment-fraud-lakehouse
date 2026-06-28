-- Current-state card dimension (latest record from the SCD2 snapshot), enriched
-- with account attributes. Use card_snapshot directly for as-of-time joins.
with current_cards as (
    select * from {{ ref('card_snapshot') }}
    where dbt_valid_to is null
),

accounts as (
    select * from {{ ref('stg_accounts') }}
)

select
    c.card_id,
    c.account_id,
    c.card_type,
    c.home_country,
    c.is_active,
    a.risk_band       as account_risk_band,
    a.signup_date     as account_signup_date
from current_cards c
left join accounts a on c.account_id = a.account_id
