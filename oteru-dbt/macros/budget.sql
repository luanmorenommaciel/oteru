{#- #48: helpers da camada de budget. -#}

{#- Slug de time/projeto da #46. Literal IDÊNTICO a CHARGEBACK_SLUG em
    oteru-collector/privacy/compile.py (um teste do Alicerce/CI pode comparar). -#}
{% macro chargeback_slug_regex() %}^[a-z0-9][a-z0-9-]{0,62}${% endmacro %}

{#- scope_key (spec §3.1): 'global' | team | project | team + '/' + project.
    Não é ambíguo porque o slug não contém '/'. -#}
{% macro budget_scope_key(scope, team, project) -%}
multiIf({{ scope }} = 'global', 'global', {{ scope }} = 'team', {{ team }}, {{ scope }} = 'project', {{ project }}, concat({{ team }}, '/', {{ project }}))
{%- endmacro %}

{#- Regra de status S2: só inteiros, Int128 contra overflow, sem divisão. -#}
{% macro budget_status_expr(consumed, budget, warn_milli, breach_milli) -%}
multiIf(
  {{ budget }} = 0, 'no_budget',
  toInt128({{ consumed }}) * 1000 >= toInt128({{ budget }}) * {{ breach_milli }}, 'breach',
  toInt128({{ consumed }}) * 1000 >= toInt128({{ budget }}) * {{ warn_milli }}, 'warn',
  'ok')
{%- endmacro %}

{#- #48 audit_budget_runs: agregado por run × scope × scope_key × status.
    count = nº de dias do período corrente de cada scope_key com aquele status. -#}
{% macro budget_audit_select(run_id_sql, run_status_sql) -%}
WITH fct AS (
  SELECT day_utc, period_start, scope, scope_key, team, project, budget_status
  FROM {{ ref('fct_budget_consumption_daily') }}
),
current_period AS (
  SELECT scope, scope_key, max(period_start) AS p FROM fct GROUP BY scope, scope_key
)
SELECT
  CAST({{ run_id_sql }} AS String) AS run_id,
  CAST(now64(3, 'UTC') AS DateTime64(3, 'UTC')) AS run_completed_at,
  CAST({{ run_status_sql }} AS LowCardinality(String)) AS run_status,
  CAST(f.scope AS LowCardinality(String)) AS scope,
  CAST(f.scope_key AS String) AS scope_key,
  CAST(any(f.team) AS String) AS team,
  CAST(any(f.project) AS String) AS project,
  CAST(f.budget_status AS LowCardinality(String)) AS status,
  CAST(count() AS UInt64) AS count
FROM fct AS f
INNER JOIN current_period AS c ON f.scope = c.scope AND f.scope_key = c.scope_key AND f.period_start = c.p
GROUP BY f.scope, f.scope_key, f.budget_status
{%- endmacro %}

{#- #48 on-run-end: grava audit_budget_runs no FIM do run, com run_status do run inteiro:
    'success' só se nenhum nó terminou em error/fail/skipped/runtime error; senão 'error'.
    Só grava se o run selecionou fct_budget_consumption_daily ou audit_budget_runs e a
    tabela existe. Não toca audit_cost_runs. -#}
{% macro log_budget_audit(results) %}
  -- depends_on: {{ ref('fct_budget_consumption_daily') }}
  {% if execute %}
    {% set budget_nodes = ['fct_budget_consumption_daily', 'audit_budget_runs'] %}
    {% set touched = results | selectattr('node.name', 'in', budget_nodes) | list %}
    {% set rel = adapter.get_relation(database=none, schema=target.schema, identifier='audit_budget_runs') %}
    {% if touched and rel is not none %}
      {% set bad = results | map(attribute='status') | map('string') | map('lower')
                   | select('in', ['error', 'fail', 'skipped', 'runtime error', 'runstatus.error', 'teststatus.fail', 'runstatus.skipped']) | list %}
      {% set run_status = 'error' if bad else 'success' %}
      {% set q %}
        INSERT INTO {{ rel }} (run_id, run_completed_at, run_status, scope, scope_key, team, project, status, count)
        {{ budget_audit_select("'" ~ invocation_id ~ "'", "'" ~ run_status ~ "'") }}
      {% endset %}
      {% do run_query(q) %}
      {{ log("Budget audit recorded in audit_budget_runs (run_status=" ~ run_status ~ ")", info=True) }}
    {% endif %}
  {% endif %}
  SELECT 1
{% endmacro %}
