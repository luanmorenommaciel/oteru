{#-
  #46 fase 1: métricas de chargeback por run (escrita: Modelo; alerta: Vigia).
  Tabela companheira de otel.audit_cost_runs (mesmo run_id = invocation_id).
  Não alteramos audit_cost_runs: o INSERT existente em log_run_audit é
  posicional (sem lista de colunas), então adicionar colunas lá quebraria o
  hook. Append-only: uma leva de linhas por run.
  Métricas:
    - actor_map_hmac_version_mismatch_rows: linhas do actor_map ativo cujo
      prefixo não é hmac:v1: (alerta Vigia se > 0). day_utc = dia UTC do run.
    - unassigned_share_by_source: por dia (últimos 8 dias) e attribution_source,
      participação do custo do dia (0..1). Sempre emite as 4 fontes.
-#}
{{
  config(
    materialized='incremental',
    incremental_strategy='append',
    engine='MergeTree()',
    order_by='(run_id, metric_name, day_utc, attribution_source)',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

WITH mismatch AS (
  SELECT countIf(NOT startsWith(actor_id, 'hmac:v1:')) AS n
  FROM {{ ref(var('chargeback_actor_map_seed', 'chargeback_actor_map')) }}
),

attributed AS (
  SELECT day_utc, toString(attribution_source) AS src, cost_usd_micros
  FROM {{ ref('int_cost_attributed') }}
  WHERE day_utc >= today() - 7
),

days AS (
  SELECT day_utc, sum(cost_usd_micros) AS day_total
  FROM attributed
  GROUP BY day_utc
),

grid AS (
  SELECT d.day_utc, d.day_total, s AS src
  FROM days AS d
  ARRAY JOIN ['otel_attr', 'service_map', 'actor_map', 'unassigned'] AS s
),

by_source AS (
  SELECT day_utc, src, sum(cost_usd_micros) AS src_total, toUInt8(1) AS hit
  FROM attributed
  GROUP BY day_utc, src
),

shares AS (
  SELECT
    g.day_utc AS day_utc,
    g.src AS src,
    if(g.day_total = 0, 0, if(b.hit = 1, b.src_total, 0) / g.day_total) AS share
  FROM grid AS g
  LEFT JOIN by_source AS b ON g.day_utc = b.day_utc AND g.src = b.src
)

SELECT
  CAST('{{ invocation_id }}' AS String) AS run_id,
  CAST(now64(9, 'UTC') AS DateTime64(9, 'UTC')) AS run_completed_at,
  CAST('actor_map_hmac_version_mismatch_rows' AS LowCardinality(String)) AS metric_name,
  CAST(toDate(now('UTC')) AS Date) AS day_utc,
  CAST('' AS LowCardinality(String)) AS attribution_source,
  CAST(n AS Float64) AS metric_value
FROM mismatch

UNION ALL

SELECT
  CAST('{{ invocation_id }}' AS String) AS run_id,
  CAST(now64(9, 'UTC') AS DateTime64(9, 'UTC')) AS run_completed_at,
  CAST('unassigned_share_by_source' AS LowCardinality(String)) AS metric_name,
  CAST(day_utc AS Date) AS day_utc,
  CAST(src AS LowCardinality(String)) AS attribution_source,
  CAST(share AS Float64) AS metric_value
FROM shares
