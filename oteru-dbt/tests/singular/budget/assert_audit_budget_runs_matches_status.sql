-- #48: contagens do último run de audit_budget_runs = group by do status da fct no
-- período corrente de cada scope_key. Em dbt build, o último run é este invocation.
WITH last_run AS (
  SELECT run_id FROM {{ ref('audit_budget_runs') }} ORDER BY run_completed_at DESC, run_id LIMIT 1
),
a AS (
  SELECT toString(scope) AS scope, scope_key, toString(status) AS status, count AS n, toUInt8(1) AS a_present
  FROM {{ ref('audit_budget_runs') }} WHERE run_id IN (SELECT run_id FROM last_run)
),
f AS (
  SELECT toString(scope) AS scope, scope_key, toString(budget_status) AS status, count() AS n, toUInt8(1) AS f_present
  FROM {{ ref('fct_budget_consumption_daily') }} AS x
  WHERE period_start = (SELECT max(period_start) FROM {{ ref('fct_budget_consumption_daily') }} AS y WHERE y.scope = x.scope AND y.scope_key = x.scope_key)
  GROUP BY scope, scope_key, status
)
SELECT if(a.a_present = 1, a.scope, f.scope) AS scope, if(a.a_present = 1, a.scope_key, f.scope_key) AS scope_key,
       if(a.a_present = 1, a.status, f.status) AS status, a.n AS audit_n, f.n AS fct_n
FROM a FULL OUTER JOIN f ON a.scope = f.scope AND a.scope_key = f.scope_key AND a.status = f.status
WHERE a.a_present = 0 OR f.f_present = 0 OR a.n != f.n
