{{
  config(
    materialized='view'
  )
}}

WITH daily_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd) AS daily_cost_usd,
    SUM(requests) AS daily_requests
  FROM {{ ref('fct_cost_daily') }}
  GROUP BY day_utc
),

with_moving_avg AS (
  SELECT
    day_utc,
    daily_cost_usd,
    daily_requests,
    AVG(daily_cost_usd) OVER (
      ORDER BY day_utc
      ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS cost_7d_avg,
    lagInFrame(daily_cost_usd, 7) OVER (
      ORDER BY day_utc
    ) AS cost_7d_ago
  FROM daily_totals
)

SELECT
  day_utc,
  daily_cost_usd,
  daily_requests,
  cost_7d_avg,
  cost_7d_ago,
  CASE
    WHEN cost_7d_ago > 0 THEN
      ((daily_cost_usd - cost_7d_ago) / cost_7d_ago) * 100
    ELSE NULL
  END AS cost_wow_change_pct
FROM with_moving_avg
ORDER BY day_utc
