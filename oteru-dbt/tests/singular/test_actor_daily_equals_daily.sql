-- Compare actor vs permanent daily totals only for days still inside the
-- actor TTL window. Fixture events are July 2025; with the default 90-day
-- TTL those actor rows expire, so a full-history join would fail even when
-- the in-window grains match. day_utc >= today() - ttl_days.
--
-- Proof run: --vars '{"actor_cost_ttl_days": 1000}' so July 2025 is in-window
-- and the equality numbers are visible. Default 90 is shown separately via
-- SHOW CREATE TABLE (TTL day_utc + toIntervalDay(90)).
{% set ttl_days = var('actor_cost_ttl_days', 90) %}

WITH actor_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS total_cost_micros
  FROM {{ ref('fct_cost_actor_daily') }}
  WHERE day_utc >= today() - toIntervalDay({{ ttl_days }})
  GROUP BY day_utc
),

daily_totals AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS total_cost_micros
  FROM {{ ref('fct_cost_daily') }}
  WHERE day_utc >= today() - toIntervalDay({{ ttl_days }})
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
