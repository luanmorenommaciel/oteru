-- #48 S3: budget_usd_micros >= 0 no seed.
SELECT scope, team, project, valid_from, budget_usd_micros
FROM {{ ref('budgets') }}
WHERE budget_usd_micros < 0
