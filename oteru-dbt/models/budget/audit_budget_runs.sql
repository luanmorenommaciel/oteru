{#-
  #48: um registro por run × scope × scope_key × status (pedido do Vigia).
  Só agregados: count = nº de dias do período corrente (de cada scope_key) com
  aquele status. Append por invocation_id. Não toca audit_cost_runs (#45/#47).
-#}
{{
  config(
    materialized='incremental',
    engine='MergeTree()',
    order_by='(run_completed_at, run_id, scope, scope_key, status)',
    incremental_strategy='append',
    on_schema_change='fail',
    contract={'enforced': true}
  )
}}

WITH fct AS (
  SELECT day_utc, period_start, scope, scope_key, team, project, budget_status
  FROM {{ ref('fct_budget_consumption_daily') }}
),

current_period AS (
  SELECT scope, scope_key, max(period_start) AS p
  FROM fct GROUP BY scope, scope_key
)

SELECT
  CAST('{{ invocation_id }}' AS String) AS run_id,
  CAST(now64(3) AS DateTime64(3)) AS run_completed_at,
  CAST(f.scope AS LowCardinality(String)) AS scope,
  CAST(f.scope_key AS String) AS scope_key,
  CAST(any(f.team) AS String) AS team,
  CAST(any(f.project) AS String) AS project,
  CAST(f.budget_status AS LowCardinality(String)) AS status,
  CAST(count() AS UInt64) AS count
FROM fct AS f
INNER JOIN current_period AS c ON f.scope = c.scope AND f.scope_key = c.scope_key AND f.period_start = c.p
GROUP BY f.scope, f.scope_key, f.budget_status
