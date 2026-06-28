-- Feature engineering: one row per transaction with the behavioural features a
-- fraud model learns from. These are deliberately the SAME features computed in
-- databricks/streaming_score.py so batch and real-time scoring agree.
--
-- IMPORTANT: the ground-truth label (is_fraud_label) is carried through for
-- training/evaluation only. It is excluded from the feature set the model sees.
with txn as (
    select * from {{ ref('stg_transactions') }}
),

cards as (
    select * from {{ ref('stg_cards') }}
),

merchants as (
    select * from {{ ref('stg_merchants') }}
),

joined as (
    select
        t.transaction_id,
        t.card_id,
        t.merchant_id,
        c.account_id,
        t.amount_usd,
        t.event_ts,
        t.event_date,
        t.entry_mode,
        t.device_id,
        t.lat,
        t.lon,
        c.home_lat,
        c.home_lon,
        c.primary_device_id,
        m.merchant_category,
        m.baseline_fraud_rate,
        t.is_fraud_label,
        t.fraud_type_label
    from txn t
    left join cards c on t.card_id = c.card_id
    left join merchants m on t.merchant_id = m.merchant_id
),

windowed as (
    select
        *,
        lag(event_ts) over (partition by card_id order by event_ts) as prev_ts,
        -- rolling stats for amount anomaly (z-score vs the card's own history)
        avg(amount_usd) over (
            partition by card_id order by event_ts
            rows between 50 preceding and 1 preceding
        ) as card_amount_mean,
        stddev(amount_usd) over (
            partition by card_id order by event_ts
            rows between 50 preceding and 1 preceding
        ) as card_amount_std,
        -- velocity: transactions by this card in the last 60 / 3600 seconds
        count(*) over (
            partition by card_id order by cast(event_ts as long)
            range between 60 preceding and current row
        ) as txn_count_60s,
        count(*) over (
            partition by card_id order by cast(event_ts as long)
            range between 3600 preceding and current row
        ) as txn_count_1h
    from joined
)

select
    transaction_id,
    card_id,
    merchant_id,
    account_id,
    merchant_category,
    event_ts,
    event_date,
    entry_mode,
    amount_usd,

    -- ---- features ----------------------------------------------------------
    ln(1 + amount_usd)                                          as amount_log,
    coalesce(unix_timestamp(event_ts) - unix_timestamp(prev_ts), 999999)
                                                               as seconds_since_prev,
    {{ haversine_km('lat', 'lon', 'home_lat', 'home_lon') }}   as geo_distance_km,
    case when device_id <> primary_device_id then 1 else 0 end as is_new_device,
    coalesce(baseline_fraud_rate, 0)                           as mcc_risk,
    case
        when card_amount_std is null or card_amount_std = 0 then 0
        else (amount_usd - card_amount_mean) / card_amount_std
    end                                                        as amount_zscore,
    txn_count_60s,
    txn_count_1h,
    -- implied travel speed: huge values => physically impossible movement
    case
        when coalesce(unix_timestamp(event_ts) - unix_timestamp(prev_ts), 0) <= 0 then 0
        else {{ haversine_km('lat', 'lon', 'home_lat', 'home_lon') }}
             / ((unix_timestamp(event_ts) - unix_timestamp(prev_ts)) / 3600.0)
    end                                                        as implied_speed_kmh,

    -- ---- label (evaluation only) -------------------------------------------
    is_fraud_label,
    fraud_type_label
from windowed
