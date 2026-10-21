{#-
  #46 fase 1: chargeback diário PERMANENTE (sem TTL, sem actor_id).
  Custo atribuído via actor_map NUNCA entra aqui: é somado em
  team/project/owner_team/attribution_source = 'unassigned' (spec §2), para
  que nenhum agregado permanente equivalha a dado individual.
  delete+insert por day_utc: o lookback recalcula dias inteiros, então uma
  mudança de seed dentro da janela substitui as linhas antigas (sem duplicar).
-#}
{{
  config(
    materialized='incremental',
    engine='ReplacingMergeTree()',
    order_by='(day_utc, team, project, owner_team, attribution_source)',
    unique_key='day_utc',
    incremental_strategy='delete+insert',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

WITH src AS (
  SELECT *
  FROM {{ ref('int_cost_attributed') }}
  {% if is_incremental() %}
  -- Tabela vazia (count=0) carrega tudo; max() em tabela vazia não pode zerar a janela.
  WHERE (SELECT count() FROM {{ this }}) = 0
     OR day_utc >= (
          SELECT max(day_utc) - INTERVAL {{ var('cost_lookback_hours') }} HOUR
          FROM {{ this }}
        )
  {% endif %}
),

folded AS (
  SELECT
    day_utc,
    if(attribution_source = 'actor_map', 'unassigned', team) AS team_f,
    if(attribution_source = 'actor_map', 'unassigned', project) AS project_f,
    if(attribution_source = 'actor_map', 'unassigned', owner_team) AS owner_team_f,
    if(attribution_source = 'actor_map', 'unassigned', toString(attribution_source)) AS source_f,
    dedup_key,
    cost_usd_micros,
    input_tokens,
    output_tokens,
    cache_read_tokens,
    cache_creation_tokens
  FROM src
),

agg AS (
  SELECT
    day_utc,
    team_f,
    project_f,
    owner_team_f,
    source_f,
    sum(cost_usd_micros) AS micros,
    uniqExact(dedup_key) AS n_requests,
    sum(input_tokens) AS in_tok,
    sum(output_tokens) AS out_tok,
    sum(cache_read_tokens) AS cr_tok,
    sum(cache_creation_tokens) AS cc_tok
  FROM folded
  GROUP BY day_utc, team_f, project_f, owner_team_f, source_f
)

SELECT
  CAST(day_utc AS Date) AS day_utc,
  CAST(team_f AS String) AS team,
  CAST(project_f AS String) AS project,
  CAST(owner_team_f AS String) AS owner_team,
  CAST(source_f AS LowCardinality(String)) AS attribution_source,
  CAST(micros AS Int64) AS cost_usd_micros,
  CAST(micros / 1e6 AS Float64) AS cost_usd,
  CAST(n_requests AS UInt64) AS requests,
  CAST(in_tok AS Int64) AS input_tokens,
  CAST(out_tok AS Int64) AS output_tokens,
  CAST(cr_tok AS Int64) AS cache_read_tokens,
  CAST(cc_tok AS Int64) AS cache_creation_tokens
FROM agg
