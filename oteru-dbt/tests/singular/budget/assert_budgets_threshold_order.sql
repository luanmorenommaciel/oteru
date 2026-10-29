-- #48 S4: 0 < warn_milli < breach_milli em toda linha do seed.
SELECT scope, team, project, valid_from, warn_milli, breach_milli
FROM {{ ref('budgets') }}
WHERE NOT (warn_milli > 0 AND warn_milli < breach_milli)
