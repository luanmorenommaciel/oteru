-- Test that sum of by-model cost equals daily total per day (EXACT equality in micros)
WITH by_model_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS total_cost_micros
  FROM {{ ref('fct_cost_by_model_daily') }}
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
    COALESCE(b.day_utc, d.day_utc) AS day_utc,
    COALESCE(b.total_cost_micros, 0) AS by_model_cost,
    COALESCE(d.total_cost_micros, 0) AS daily_cost,
    ABS(COALESCE(b.total_cost_micros, 0) - COALESCE(d.total_cost_micros, 0)) AS diff_micros
  FROM by_model_totals b
  FULL OUTER JOIN daily_totals d
    ON b.day_utc = d.day_utc
  WHERE diff_micros > 0
)

SELECT
  day_utc,
  by_model_cost,
  daily_cost,
  diff_micros
FROM comparison
