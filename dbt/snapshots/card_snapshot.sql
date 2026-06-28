{% snapshot card_snapshot %}
{{
    config(
        target_schema='silver',
        unique_key='card_id',
        strategy='check',
        check_cols=['is_active', 'card_type', 'home_country'],
        invalidate_hard_deletes=True,
    )
}}
-- SCD Type 2 history for cards: captures when a card is blocked/reactivated or
-- re-issued. Lets marts answer "was this card active *at the time* of the txn".
select
    card_id,
    account_id,
    card_type,
    primary_device_id,
    home_country,
    is_active
from {{ ref('stg_cards') }}
{% endsnapshot %}
