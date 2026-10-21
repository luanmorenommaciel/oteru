{#-
  #48: o que o alerta lê (spec §4). 1 linha por scope × scope_key, no último dia
  processado (max(day_utc) de fct_budget_consumption_daily).
  - as_of_ts (S1) = high-water mark GLOBAL de custo: max(last_cost_ts) da fct,
    que vem de max(event_ts) de int_cost_attributed. Nunca now()/loaded_at.
  - is_stale = as_of_ts mais velho que budget_stale_hours_daily (avaliado na
    leitura: por isso é view). Fail-open: is_stale não muda budget_status.
  - S5: SQL SECURITY DEFINER; o definer (criado fora do dbt, Alicerce §7.1) só
    precisa de SELECT em fct_budget_consumption_daily. A view não lê outra relação.
-#}
{{
  config(
    materialized='view',
    sql_security='definer',
    definer=var('budget_view_definer', 'oteru_budget_view_definer'),
    contract={'enforced': true}
  )
}}

WITH fct AS (
  SELECT
    day_utc, period_start, scope, scope_key, team, project, owner_team,
    budget_usd_micros, consumed_period_usd_micros, consumed_milli,
    warn_milli, breach_milli, budget_status, action_on_breach, last_cost_ts
  FROM {{ ref('fct_budget_consumption_daily') }}
),

wm AS (
  SELECT max(day_utc) AS last_day, max(last_cost_ts) AS as_of_ts FROM fct
)

SELECT
  CAST(f.scope AS LowCardinality(String)) AS scope,
  CAST(f.scope_key AS String) AS scope_key,
  CAST(f.team AS String) AS team,
  CAST(f.project AS String) AS project,
  CAST(f.owner_team AS String) AS owner_team,
  CAST(f.period_start AS Date) AS period_start,
  CAST(f.day_utc AS Date) AS as_of_day,
  CAST(w.as_of_ts AS DateTime64(9)) AS as_of_ts,
  CAST(f.budget_usd_micros AS Int64) AS budget_usd_micros,
  CAST(f.consumed_period_usd_micros AS Int64) AS consumed_period_usd_micros,
  CAST(f.consumed_milli AS Int64) AS consumed_milli,
  CAST(f.warn_milli AS UInt16) AS warn_milli,
  CAST(f.breach_milli AS UInt16) AS breach_milli,
  CAST(f.budget_status AS LowCardinality(String)) AS budget_status,
  CAST(f.action_on_breach AS LowCardinality(String)) AS action_on_breach,
  CAST({{ var('budget_stale_hours_daily', 26) }} AS UInt16) AS stale_threshold_hours,
  CAST(w.as_of_ts < now64(9, 'UTC') - toIntervalHour({{ var('budget_stale_hours_daily', 26) }}) AS Bool) AS is_stale
FROM fct AS f
CROSS JOIN wm AS w
WHERE f.day_utc = w.last_day
