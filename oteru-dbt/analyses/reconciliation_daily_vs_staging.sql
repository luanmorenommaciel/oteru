-- Reconciliation: daily total vs staging within max(0.5%, 10000 micros) per closed UTC day
-- A closed day is one that is at least 1 day in the past (to allow late arrivals)
WITH staging_daily AS (
  SELECT
    toDate(event_ts, 'UTC') AS day_utc,
    SUM(cost_usd_micros) AS staging_cost_micros,
    COUNT(DISTINCT dedup_key) AS staging_requests
  FROM {{ source('otel', 'stg_api_request') }}
  WHERE toDate(event_ts, 'UTC') < toDate(now(), 'UTC')
  GROUP BY day_utc
),

aggregate_daily AS (
  SELECT
    day_utc,
    SUM(cost_usd_micros) AS aggregate_cost_micros,
    SUM(requests) AS aggregate_requests
  FROM {{ ref('fct_cost_daily') }}
  WHERE day_utc < toDate(now(), 'UTC')
  GROUP BY day_utc
),

comparison AS (
  SELECT
    COALESCE(s.day_utc, a.day_utc) AS day_utc,
    COALESCE(s.staging_cost_micros, 0) AS staging_cost_micros,
    COALESCE(a.aggregate_cost_micros, 0) AS aggregate_cost_micros,
    COALESCE(s.staging_requests, 0) AS staging_requests,
    COALESCE(a.aggregate_requests, 0) AS aggregate_requests,
    ABS(COALESCE(s.staging_cost_micros, 0) - COALESCE(a.aggregate_cost_micros, 0)) AS diff_micros,
    CASE
      WHEN s.staging_cost_micros > 0 THEN
        (ABS(COALESCE(s.staging_cost_micros, 0) - COALESCE(a.aggregate_cost_micros, 0)) / s.staging_cost_micros) * 100
      ELSE 0
    END AS diff_pct
  FROM staging_daily s
  FULL OUTER JOIN aggregate_daily a
    ON s.day_utc = a.day_utc
)

SELECT
  day_utc,
  staging_cost_micros,
  aggregate_cost_micros,
  diff_micros,
  diff_pct,
  staging_requests,
  aggregate_requests,
  CASE
    WHEN diff_micros > 10000 AND diff_pct > 0.5 THEN 'FAIL'
    ELSE 'PASS'
  END AS reconciliation_status
FROM comparison
WHERE diff_micros > 10000 AND diff_pct > 0.5
ORDER BY day_utc
