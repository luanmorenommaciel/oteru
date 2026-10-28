-- #48 §9 item 3: coerência scope × campos.
SELECT scope, team, project, valid_from
FROM {{ ref('budgets') }}
WHERE NOT multiIf(
  scope = 'global',       team = '' AND project = '',
  scope = 'team',         team != '' AND project = '',
  scope = 'project',      team = '' AND project != '',
  scope = 'team_project', team != '' AND project != '',
  0)
