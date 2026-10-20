{{
  config(
    materialized='incremental',
    engine='ReplacingMergeTree()',
    order_by='(day_utc, actor_id)',
    unique_key=['day_utc', 'actor_id'],
    incremental_strategy='delete+insert',
    clickhouse__engine_clause='ENGINE = ReplacingMergeTree() ORDER BY (day_utc, actor_id) TTL day_utc + INTERVAL {{ var("actor_cost_ttl_days") }} DAY'
  )
}}

WITH daily_aggregates AS (
  SELECT
    toDate(event_ts, 'UTC') AS day_utc,
    actor_id,
    SUM(cost_usd_micros) AS cost_usd_micros,
    COUNT(DISTINCT dedup_key) AS requests,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cache_read_tokens) AS cache_read_tokens,
    SUM(cache_creation_tokens) AS cache_creation_tokens
  FROM {{ ref('int_api_request_dedup') }}
  {% if is_incremental() %}
  WHERE toDate(event_ts, 'UTC') >= (
    SELECT max(day_utc) - INTERVAL {{ var('cost_lookback_hours') / 24 }} DAY
    FROM {{ this }}
  )
  {% endif %}
  GROUP BY day_utc, actor_id
)

SELECT
  day_utc,
  actor_id,
  cost_usd_micros,
  cost_usd_micros / 1e6 AS cost_usd,
  requests,
  input_tokens,
  output_tokens,
  cache_read_tokens,
  cache_creation_tokens
FROM daily_aggregates
