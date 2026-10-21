-- #48 CA1: vigência sem sobreposição por (scope, team, project).
-- valid_to é inclusive; NULL = aberto. Também falha se valid_to < valid_from.
WITH b AS (
  SELECT scope, team, project, valid_from, ifNull(valid_to, toDate('2149-06-06')) AS vt
  FROM {{ ref('budgets') }}
)
SELECT a.scope, a.team, a.project, a.valid_from AS a_from, a.vt AS a_to, c.valid_from AS b_from, c.vt AS b_to,
       'vigências sobrepostas' AS issue
FROM b AS a
INNER JOIN b AS c ON a.scope = c.scope AND a.team = c.team AND a.project = c.project
WHERE a.valid_from < c.valid_from AND c.valid_from <= a.vt
UNION ALL
SELECT scope, team, project, valid_from, vt, valid_from, vt, 'valid_to < valid_from'
FROM b WHERE vt < valid_from
