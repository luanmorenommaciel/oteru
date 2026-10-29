-- #48 §3.1: scope_key = regra do scope em seed (via stg), fct, view e audit;
-- e coerência scope × campos em todas elas.
{% set rels = [('stg_budgets', 'scope'), ('fct_budget_consumption_daily', 'scope'), ('budget_status_current', 'scope'), ('audit_budget_runs', 'scope')] %}
{% for r, s in rels %}
SELECT '{{ r }}' AS model, toString(scope) AS scope, scope_key, team, project
FROM {{ ref(r) }}
WHERE scope_key != {{ budget_scope_key('toString(scope)', 'team', 'project') }}
   OR NOT multiIf(scope = 'global', team = '' AND project = '', scope = 'team', team != '' AND project = '',
                  scope = 'project', team = '' AND project != '', scope = 'team_project', team != '' AND project != '', 0)
{% if not loop.last %}UNION ALL{% endif %}
{% endfor %}
