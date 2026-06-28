-- Central transaction fact. Grain: one row per transaction.
-- Combines engineered features, the authorisation outcome, and the (late)
-- chargeback confirmation, alongside the generator ground-truth label.
{{ config(
    materialized='incremental',
    unique_key='transaction_id',
    incremental_strategy='merge',
    on_schema_change='append_new_columns'
) }}

with enriched as (
    select * from {{ ref('int_transactions_enriched') }}
    {% if is_incremental() %}
      where event_date >= (select coalesce(max(event_date), '1900-01-01') from {{ this }})
    {% endif %}
),

auth as (
    select * from {{ ref('stg_auth_events') }}
),

chargebacks as (
    select transaction_id, true as has_chargeback, reason_code
    from {{ ref('stg_chargebacks') }}
)

select
    e.transaction_id,
    e.card_id,
    e.merchant_id,
    e.account_id,
    e.merchant_category,
    e.event_ts,
    e.event_date,
    hour(e.event_ts)              as event_hour,
    e.entry_mode,
    e.amount_usd,

    -- features (also feed ML)
    e.amount_log,
    e.seconds_since_prev,
    e.geo_distance_from_home_km,
    e.geo_distance_from_prev_km,
    e.is_new_device,
    e.mcc_risk,
    e.amount_zscore,
    e.txn_count_60s,
    e.txn_count_1h,
    e.implied_speed_kmh,

    -- outcomes
    coalesce(a.is_approved, true)              as is_approved,
    a.decline_reason,
    coalesce(c.has_chargeback, false)          as is_confirmed_fraud,
    c.reason_code                              as chargeback_reason,

    -- ground truth (evaluation only)
    e.is_fraud_label,
    e.fraud_type_label
from enriched e
left join auth a on e.transaction_id = a.transaction_id
left join chargebacks c on e.transaction_id = c.transaction_id
