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
