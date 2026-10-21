{#-
  #46 fase 1: chargeback por ator (pseudônimo hmac:v1:), TTL
  day_utc + actor_cost_ttl_days (90). Único lugar, além de int_cost_attributed,
  onde attribution_source='actor_map' aparece com o time mapeado.
  Grão: day_utc × team × project × actor_id.
-#}
{{
  config(
    materialized='incremental',
    engine='ReplacingMergeTree()',
    order_by='(day_utc, team, project, actor_id)',
    unique_key='day_utc',
    incremental_strategy='delete+insert',
    ttl='day_utc + INTERVAL ' ~ var('actor_cost_ttl_days') ~ ' DAY',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

WITH src AS (
  SELECT *
  FROM {{ ref('int_cost_attributed') }}
  WHERE day_utc >= today() - {{ var('actor_cost_ttl_days') }}
  {% if is_incremental() %}
    AND (
      (SELECT count() FROM {{ this }}) = 0
      OR day_utc >= (
           SELECT max(day_utc) - INTERVAL {{ var('cost_lookback_hours') }} HOUR
           FROM {{ this }}
         )
    )
  {% endif %}
),

agg AS (
  SELECT
    day_utc,
    team,
    project,
    actor_id,
    sum(cost_usd_micros) AS micros,
    uniqExact(dedup_key) AS n_requests,
    sum(input_tokens) AS in_tok,
    sum(output_tokens) AS out_tok,
    sum(cache_read_tokens) AS cr_tok,
    sum(cache_creation_tokens) AS cc_tok
  FROM src
  GROUP BY day_utc, team, project, actor_id
)

SELECT
  CAST(day_utc AS Date) AS day_utc,
  CAST(team AS String) AS team,
  CAST(project AS String) AS project,
  CAST(actor_id AS String) AS actor_id,
  CAST(micros AS Int64) AS cost_usd_micros,
  CAST(micros / 1e6 AS Float64) AS cost_usd,
  CAST(n_requests AS UInt64) AS requests,
  CAST(in_tok AS Int64) AS input_tokens,
  CAST(out_tok AS Int64) AS output_tokens,
  CAST(cr_tok AS Int64) AS cache_read_tokens,
  CAST(cc_tok AS Int64) AS cache_creation_tokens
FROM agg
