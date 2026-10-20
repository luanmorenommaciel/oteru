-- Per-day exact micros: sum(fct_cost_actor_daily) == sum(fct_cost_daily).
-- NEVER pass empty-vs-empty: 0 actor rows in scope is a FAIL even if daily
-- is also empty. Proof loads the fixture with timestamps shifted near now()
-- (default 90-day TTL keeps those rows). Do not edit the teammate fixture.

SELECT
  toDate('1970-01-01') AS day_utc,
  0 AS actor_cost,
  (SELECT coalesce(sum(cost_usd_micros), 0) FROM {{ ref('fct_cost_daily') }}) AS daily_cost,
  toInt64(1) AS diff_micros
WHERE (SELECT count() FROM {{ ref('fct_cost_actor_daily') }}) = 0

UNION ALL

SELECT
  day_utc,
  actor_cost,
  daily_cost,
  diff_micros
FROM (
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
  )

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
