-- A chargeback can never settle before the transaction it disputes occurred.
-- Catches join/key errors and bad timestamp handling.
select
    cb.chargeback_id,
    cb.settle_ts,
    t.event_ts
from {{ ref('stg_chargebacks') }} cb
join {{ ref('stg_transactions') }} t on cb.transaction_id = t.transaction_id
where cb.settle_ts < t.event_ts
