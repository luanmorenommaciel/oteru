-- #48 S2: budget_status materializado = regra inteira recalculada (Int128).
SELECT day_utc, scope, scope_key, budget_status
FROM {{ ref('fct_budget_consumption_daily') }}
WHERE budget_status != {{ budget_status_expr('consumed_period_usd_micros', 'budget_usd_micros', 'warn_milli', 'breach_milli') }}
