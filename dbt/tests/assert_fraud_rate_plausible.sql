-- Guardrail: overall labelled fraud rate should sit in a believable band.
-- A breach usually means the generator config or a join fanned out the data.
with stats as (
    select
        count(*) as n,
        sum(case when is_fraud_label then 1 else 0 end) as fraud
    from {{ ref('fct_transactions') }}
)

select n, fraud, fraud / n as rate
from stats
where fraud / n > 0.05      -- > 5% labelled fraud is implausibly high
   or fraud = 0             -- zero fraud means injection silently failed
