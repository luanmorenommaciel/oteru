-- #48: unicidade (run_id, scope, scope_key, status) em audit_budget_runs.
SELECT run_id, scope, scope_key, status, count() AS n
FROM {{ ref('audit_budget_runs') }}
GROUP BY run_id, scope, scope_key, status HAVING n > 1
