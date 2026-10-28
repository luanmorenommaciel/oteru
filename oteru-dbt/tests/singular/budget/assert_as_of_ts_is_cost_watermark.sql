-- #48 S1: as_of_ts = max(event_ts) do recorte processado (int_cost_attributed nos dias
-- presentes na fct) e as_of_ts <= max(loaded_at). Igual em todas as linhas da view.
WITH v AS (SELECT min(as_of_ts) AS lo, max(as_of_ts) AS hi, count() AS n FROM {{ ref('budget_status_current') }}),
src AS (
  SELECT max(event_ts) AS m FROM {{ ref('int_cost_attributed') }}
  WHERE day_utc IN (SELECT day_utc FROM {{ ref('fct_budget_consumption_daily') }})
),
ld AS (SELECT max(loaded_at) AS m FROM {{ ref('fct_budget_consumption_daily') }})
SELECT v.lo, v.hi, src.m AS cost_watermark, ld.m AS max_loaded_at
FROM v CROSS JOIN src CROSS JOIN ld
WHERE v.n > 0 AND (v.lo != v.hi OR v.hi != src.m OR toDateTime64(v.hi, 3) > ld.m)
