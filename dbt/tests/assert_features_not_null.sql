-- Every transaction in the fact must carry a complete feature vector — a null
-- feature would silently degrade the model at scoring time.
select transaction_id
from {{ ref('fct_transactions') }}
where amount_log is null
   or seconds_since_prev is null
   or geo_distance_from_home_km is null
   or geo_distance_from_prev_km is null
   or is_new_device is null
   or mcc_risk is null
   or implied_speed_kmh is null
