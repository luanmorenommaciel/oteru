{{
  config(
    materialized='incremental',
    engine='MergeTree()',
    order_by='(event_ts, dedup_key)',
    unique_key='dedup_key',
    settings={
      'allow_nullable_key': 1
    }
  )
}}

WITH source_data AS (
  SELECT
    event_ts,
    observed_ts,
    dedup_key,
    session_id,
    model_raw,
    cost_usd_micros,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_creation_tokens,
    actor_id,
    service_name
  FROM {{ source('otel', 'stg_api_request') }}
  {% if is_incremental() %}
  WHERE event_ts >= (
    SELECT toDateTime64(max(event_ts), 9) - INTERVAL {{ var('cost_lookback_hours') }} HOUR
    FROM {{ this }}
  )
  {% endif %}
),

deduplicated AS (
  SELECT
    event_ts,
    observed_ts,
    dedup_key,
    session_id,
    model_raw,
    cost_usd_micros,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_creation_tokens,
    actor_id,
    service_name,
    ROW_NUMBER() OVER (
      PARTITION BY dedup_key
      ORDER BY observed_ts ASC
    ) AS rn
  FROM source_data
)

SELECT
  event_ts,
  observed_ts,
  dedup_key,
  session_id,
  model_raw,
  cost_usd_micros,
  input_tokens,
  output_tokens,
  cache_read_tokens,
  cache_creation_tokens,
  actor_id,
  service_name
FROM deduplicated
WHERE rn = 1
