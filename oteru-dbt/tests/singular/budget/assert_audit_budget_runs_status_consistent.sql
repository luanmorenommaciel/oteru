-- #48: cada run_id tem um único run_status ('success' | 'error') em todas as suas linhas.
SELECT run_id, groupUniqArray(toString(run_status)) AS statuses
FROM {{ ref('audit_budget_runs') }}
GROUP BY run_id
HAVING length(statuses) != 1
