{#-
  #46 fase 1: atribuição de custo por request deduplicado.
  Precedência (primeira que casa vence):
    1. otel_attr   -> fase 2 (Duto); ainda não propagado, nunca vence na fase 1
    2. service_map -> seed chargeback_service_map (service_name)
    3. actor_map   -> seed chargeback_actor_map (actor_id hmac:v1:, exceção)
    4. unassigned  -> literal 'unassigned' em team/project/owner_team
  Custo nunca é descartado: LEFT JOIN + flag de match (ClickHouse devolve ''
  e não NULL em LEFT JOIN sem join_use_nulls, por isso usamos *_hit).
  Linhas de actor_map com versão HMAC != v1 não casam (caem em unassigned)
  e são contadas em audit_cost_runs_chargeback.
-#}
{{
  config(
    materialized='view',
    settings={},
    contract={'enforced': true}
  )
}}

WITH base AS (
  SELECT
    assumeNotNull(dedup_key) AS dedup_key,
    event_ts,
    toDate(event_ts, 'UTC') AS day_utc,
    actor_id,
    toString(service_name) AS service_name,
    toInt64(ifNull(cost_usd_micros, 0)) AS cost_usd_micros,
    toInt64(ifNull(input_tokens, 0)) AS input_tokens,
    toInt64(ifNull(output_tokens, 0)) AS output_tokens,
    toInt64(ifNull(cache_read_tokens, 0)) AS cache_read_tokens,
    toInt64(ifNull(cache_creation_tokens, 0)) AS cache_creation_tokens
  FROM {{ ref('int_api_request_dedup') }}
),

service_map AS (
  SELECT
    service_name AS sm_service_name,
    team AS sm_team,
    project AS sm_project,
    owner_team AS sm_owner_team,
    toUInt8(1) AS sm_hit
  FROM {{ ref(var('chargeback_service_map_seed', 'chargeback_service_map')) }}
),

actor_map AS (
  SELECT
    actor_id AS am_actor_id,
    team AS am_team,
    project AS am_project,
    toUInt8(1) AS am_hit
  FROM {{ ref(var('chargeback_actor_map_seed', 'chargeback_actor_map')) }}
  WHERE match(actor_id, '^hmac:v1:[0-9a-f]{64}$')
),

joined AS (
  SELECT
    b.*,
    sm.sm_team, sm.sm_project, sm.sm_owner_team, sm.sm_hit,
    am.am_team, am.am_project, am.am_hit
  FROM base AS b
  LEFT JOIN service_map AS sm ON b.service_name = sm.sm_service_name
  LEFT JOIN actor_map AS am ON b.actor_id = am.am_actor_id
)

SELECT
  CAST(dedup_key AS String) AS dedup_key,
  CAST(event_ts AS DateTime64(9)) AS event_ts,
  CAST(day_utc AS Date) AS day_utc,
  CAST(actor_id AS String) AS actor_id,
  CAST(service_name AS String) AS service_name,
  CAST(multiIf(sm_hit = 1, sm_team, am_hit = 1, am_team, 'unassigned') AS String) AS team,
  CAST(multiIf(sm_hit = 1, sm_project, am_hit = 1, am_project, 'unassigned') AS String) AS project,
  -- owner_team: actor_map não tem owner_team; o time mapeado é o responsável.
  CAST(multiIf(sm_hit = 1, sm_owner_team, am_hit = 1, am_team, 'unassigned') AS String) AS owner_team,
  CAST(multiIf(sm_hit = 1, 'service_map', am_hit = 1, 'actor_map', 'unassigned') AS LowCardinality(String)) AS attribution_source,
  CAST(cost_usd_micros AS Int64) AS cost_usd_micros,
  CAST(input_tokens AS Int64) AS input_tokens,
  CAST(output_tokens AS Int64) AS output_tokens,
  CAST(cache_read_tokens AS Int64) AS cache_read_tokens,
  CAST(cache_creation_tokens AS Int64) AS cache_creation_tokens
FROM joined
