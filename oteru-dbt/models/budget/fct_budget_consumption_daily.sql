{#-
  #48: consumo diário vs. budget por day_utc × scope × scope_key (spec §4).
  - Custo vem de fct_cost_chargeback_daily (reconciliação exata); global soma
    tudo, incluindo 'unassigned'. Sem actor_id em nenhum passo.
  - last_cost_ts = max(event_ts) de int_cost_attributed agregado por dia × team ×
    project ANTES de qualquer join (S1); mesmo fold de actor_map -> 'unassigned'
    do chargeback. Epoch (1970-01-01) quando o scope_key não teve custo no dia.
  - Dias = dias presentes em fct_cost_chargeback_daily dentro da janela; 1 linha
    por budget vigente no dia (consumo 0 se o scope_key não teve custo).
  - Incremental: recalcula do início do mês de (max(day_utc) - lookback) em
    diante (acumulado = window sobre o período inteiro); delete+insert por dia.
  - Status: regra inteira S2 (macro budget_status_expr).
-#}
{{
  config(
    materialized='incremental',
    engine='ReplacingMergeTree()',
    order_by='(day_utc, scope, scope_key)',
    unique_key='day_utc',
    incremental_strategy='delete+insert',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

WITH bounds AS (
  SELECT
    {% if is_incremental() %}
    if((SELECT count() FROM {{ this }}) = 0,
       toDate('1970-01-01'),
       toStartOfMonth(toDate((SELECT max(day_utc) FROM {{ this }}) - INTERVAL {{ var('cost_lookback_hours') }} HOUR)))
    {% else %}
    toDate('1970-01-01')
    {% endif %} AS start_day
),

cost AS (
  SELECT day_utc, team, project, sum(cost_usd_micros) AS micros
  FROM {{ ref('fct_cost_chargeback_daily') }}
  WHERE day_utc >= (SELECT start_day FROM bounds)
  GROUP BY day_utc, team, project
),

last_ts AS (
  SELECT
    day_utc,
    if(attribution_source = 'actor_map', 'unassigned', team) AS team,
    if(attribution_source = 'actor_map', 'unassigned', project) AS project,
    max(event_ts) AS max_ts
  FROM {{ ref('int_cost_attributed') }}
  WHERE day_utc >= (SELECT start_day FROM bounds)
  GROUP BY day_utc, team, project
),

cell AS (
  SELECT c.day_utc AS day_utc, c.team AS team, c.project AS project, c.micros AS micros,
         l.max_ts AS max_ts
  FROM cost AS c
  LEFT JOIN last_ts AS l ON c.day_utc = l.day_utc AND c.team = l.team AND c.project = l.project
),

-- custo por grão (um custo conta em todos os grãos que o contêm)
grain AS (
  SELECT day_utc, 'global' AS scope, 'global' AS scope_key, sum(micros) AS micros, max(max_ts) AS max_ts
  FROM cell GROUP BY day_utc
  UNION ALL
  SELECT day_utc, 'team', team, sum(micros), max(max_ts) FROM cell GROUP BY day_utc, team
  UNION ALL
  SELECT day_utc, 'project', project, sum(micros), max(max_ts) FROM cell GROUP BY day_utc, project
  UNION ALL
  SELECT day_utc, 'team_project', concat(team, '/', project), sum(micros), max(max_ts) FROM cell GROUP BY day_utc, team, project
),

days AS (
  SELECT DISTINCT day_utc FROM cost
),

budget_day AS (
  SELECT
    d.day_utc AS day_utc,
    b.scope AS scope, b.scope_key AS scope_key, b.team AS team, b.project AS project,
    b.owner_team AS owner_team, b.budget_usd_micros AS budget_usd_micros,
    b.warn_milli AS warn_milli, b.breach_milli AS breach_milli,
    b.action_on_breach AS action_on_breach
  FROM days AS d
  CROSS JOIN {{ ref('stg_budgets') }} AS b
  WHERE b.valid_from <= d.day_utc AND d.day_utc <= b.valid_to_effective
),

joined AS (
  SELECT
    bd.*,
    toInt64(if(g.has_cost = 1, g.micros, 0)) AS consumed_day,
    if(g.has_cost = 1, g.max_ts, toDateTime64(0, 9, 'UTC')) AS last_ts
  FROM budget_day AS bd
  LEFT JOIN (SELECT *, toUInt8(1) AS has_cost FROM grain) AS g
    ON bd.day_utc = g.day_utc AND toString(bd.scope) = g.scope AND bd.scope_key = g.scope_key
),

cumulative AS (
  SELECT
    *,
    toStartOfMonth(day_utc) AS p_start,
    sum(consumed_day) OVER (
      PARTITION BY scope, scope_key, toStartOfMonth(day_utc)
      ORDER BY day_utc
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS consumed_period
  FROM joined
)

SELECT
  CAST(day_utc AS Date) AS day_utc,
  CAST(p_start AS Date) AS period_start,
  CAST(scope AS LowCardinality(String)) AS scope,
  CAST(scope_key AS String) AS scope_key,
  CAST(team AS String) AS team,
  CAST(project AS String) AS project,
  CAST(owner_team AS String) AS owner_team,
  CAST(budget_usd_micros AS Int64) AS budget_usd_micros,
  CAST(consumed_day AS Int64) AS consumed_usd_micros,
  CAST(consumed_period AS Int64) AS consumed_period_usd_micros,
  CAST(if(budget_usd_micros = 0, 0, intDiv(toInt128(consumed_period) * 1000, toInt128(budget_usd_micros))) AS Int64) AS consumed_milli,
  CAST(warn_milli AS UInt16) AS warn_milli,
  CAST(breach_milli AS UInt16) AS breach_milli,
  CAST({{ budget_status_expr('consumed_period', 'budget_usd_micros', 'warn_milli', 'breach_milli') }} AS LowCardinality(String)) AS budget_status,
  CAST(action_on_breach AS LowCardinality(String)) AS action_on_breach,
  CAST(last_ts AS DateTime64(9)) AS last_cost_ts,
  CAST(intDiv(toInt128(consumed_period) * toDayOfMonth(toLastDayOfMonth(day_utc)), toDayOfMonth(day_utc)) AS Int64) AS projected_period_usd_micros,
  CAST(now64(3) AS DateTime64(3)) AS loaded_at
FROM cumulative
