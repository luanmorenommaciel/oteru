-- Freshness check: max lag 2h between aggregate's latest partition and max(event_ts) in staging
-- 6h counts as an incident
WITH staging_max AS (
  SELECT
    max(event_ts) AS max_event_ts
  FROM {{ source('otel', 'stg_api_request') }}
),

aggregate_max AS (
  SELECT
    toDateTime64(max(event_ts), 9) AS max_event_ts
  FROM {{ ref('int_api_request_dedup') }}
),

freshness AS (
  SELECT
    s.max_event_ts AS staging_max_event_ts,
    a.max_event_ts AS aggregate_max_event_ts,
    dateDiff('second', a.max_event_ts, s.max_event_ts) AS lag_seconds,
    dateDiff('second', a.max_event_ts, s.max_event_ts) / 3600.0 AS lag_hours
  FROM staging_max s
  CROSS JOIN aggregate_max a
)

SELECT
  staging_max_event_ts,
  aggregate_max_event_ts,
  lag_seconds,
  lag_hours,
  CASE
    WHEN lag_hours > 6 THEN 'INCIDENT'
    WHEN lag_hours > 2 THEN 'WARNING'
    ELSE 'OK'
  END AS freshness_status
FROM freshness
