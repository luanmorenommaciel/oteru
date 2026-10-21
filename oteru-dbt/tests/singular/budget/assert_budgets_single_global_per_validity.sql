-- #48 CA1: em qualquer dia, no máximo uma linha scope='global' vigente.
-- Explícito (redundante com o teste de sobreposição). Checa cada valid_from de global.
WITH g AS (
  SELECT valid_from, ifNull(valid_to, toDate('2149-06-06')) AS vt
  FROM {{ ref('budgets') }} WHERE scope = 'global'
)
SELECT d.valid_from AS day, count() AS globals_vigentes
FROM g AS d
CROSS JOIN g AS x
WHERE x.valid_from <= d.valid_from AND d.valid_from <= x.vt
GROUP BY d.valid_from
HAVING globals_vigentes > 1
