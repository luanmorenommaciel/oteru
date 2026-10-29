{#-
  #48: budgets tipados. scope_key derivado (spec §3.1); valid_to NULL = aberto,
  normalizado em valid_to_effective (inclusive). A resolução de override por
  vigência é feita no join por dia em fct_budget_consumption_daily (no máximo
  uma linha vigente por scope × scope_key e dia: o seed não tem sobreposição).
-#}
{{ config(materialized='view', contract={'enforced': true}) }}

SELECT
  CAST(scope AS LowCardinality(String)) AS scope,
  CAST({{ budget_scope_key('toString(scope)', 'team', 'project') }} AS String) AS scope_key,
  CAST(team AS String) AS team,
  CAST(project AS String) AS project,
  CAST(period AS LowCardinality(String)) AS period,
  CAST(budget_usd_micros AS Int64) AS budget_usd_micros,
  CAST(warn_milli AS UInt16) AS warn_milli,
  CAST(breach_milli AS UInt16) AS breach_milli,
  CAST(action_on_breach AS LowCardinality(String)) AS action_on_breach,
  CAST(valid_from AS Date) AS valid_from,
  CAST(valid_to AS Nullable(Date)) AS valid_to,
  CAST(ifNull(valid_to, toDate('2149-06-06')) AS Date) AS valid_to_effective,
  CAST(owner_team AS String) AS owner_team
FROM {{ ref('budgets') }}
