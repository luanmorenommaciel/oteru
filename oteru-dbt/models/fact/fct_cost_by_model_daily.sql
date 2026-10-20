{{
  config(
    materialized='incremental',
    engine='ReplacingMergeTree()',
    order_by='(day_utc, model_family, actor_id)',
    unique_key=['day_utc', 'model_family', 'actor_id'],
    incremental_strategy='delete+insert'
  )
}}

WITH model_mapped AS (
  SELECT
    d.event_ts,
    d.dedup_key,
    d.actor_id,
    d.model_raw,
    COALESCE(m.model_family, 'unmapped') AS model_family,
    d.cost_usd_micros,
    d.input_tokens,
    d.output_tokens,
    d.cache_read_tokens,
    d.cache_creation_tokens
  FROM {{ ref('int_api_request_dedup') }} d
  LEFT JOIN {{ ref('model_aliases') }} m
    ON d.model_raw = m.model_raw
  {% if is_incremental() %}
  WHERE toDate(d.event_ts, 'UTC') >= (
    SELECT max(day_utc) - INTERVAL 2 DAY
    FROM {{ this }}
  )
  {% endif %}
),

daily_by_model AS (
  SELECT
    toDate(event_ts, 'UTC') AS day_utc,
    model_family,
    actor_id,
    SUM(cost_usd_micros) AS cost_usd_micros,
    COUNT(DISTINCT dedup_key) AS requests,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cache_read_tokens) AS cache_read_tokens,
    SUM(cache_creation_tokens) AS cache_creation_tokens
  FROM model_mapped
  GROUP BY day_utc, model_family, actor_id
)

SELECT
  day_utc,
  model_family,
  actor_id,
  cost_usd_micros,
  cost_usd_micros / 1e6 AS cost_usd,
  requests,
  input_tokens,
  output_tokens,
  cache_read_tokens,
  cache_creation_tokens
FROM daily_by_model
