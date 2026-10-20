-- Test that sum of actor_daily cost equals permanent daily total per day (EXACT equality in micros)
WITH actor_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS total_cost_micros
  FROM {{ ref('fct_cost_actor_daily') }}
  GROUP BY day_utc
),

daily_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS total_cost_micros
  FROM {{ ref('fct_cost_daily') }}
  GROUP BY day_utc
),

comparison AS (
  SELECT
    COALESCE(a.day_utc, d.day_utc) AS day_utc,
    COALESCE(a.total_cost_micros, 0) AS actor_cost,
    COALESCE(d.total_cost_micros, 0) AS daily_cost,
    ABS(COALESCE(a.total_cost_micros, 0) - COALESCE(d.total_cost_micros, 0)) AS diff_micros
  FROM actor_totals a
  FULL OUTER JOIN daily_totals d
    ON a.day_utc = d.day_utc
  WHERE diff_micros > 0
)

SELECT
  day_utc,
  actor_cost,
  daily_cost,
  diff_micros
FROM comparison
