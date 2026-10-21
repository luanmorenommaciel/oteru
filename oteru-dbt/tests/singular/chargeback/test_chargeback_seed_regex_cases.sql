-- #46 (Sentinela, cond. 4): casos positivos/negativos do regex de seed
-- {{ chargeback_seed_actor_regex() }} (exatamente ^hmac:v[0-9]+:[0-9a-f]{64}$).
-- Falha se um caso negativo casar ou um positivo não casar. Valores sintéticos.
WITH cases AS (
  SELECT c.1 AS v, c.2 AS should_match FROM (
    SELECT arrayJoin([
      (concat('hmac:v1:', repeat('a', 64)), 1),
      (concat('hmac:v2:', repeat('0', 64)), 1),
      (concat('hmac:v10:', repeat('f', 64)), 1),
      (concat('hmac:v1:', repeat('a', 63)), 0),
      (concat('hmac:v1:', repeat('a', 65)), 0),
      (concat('hmac:v1:', repeat('A', 64)), 0),
      (concat('hmac:v1:', repeat('g', 64)), 0),
      (concat('hmac:vx:', repeat('a', 64)), 0),
      (concat('hmac:v:', repeat('a', 64)), 0),
      (concat('HMAC:v1:', repeat('a', 64)), 0),
      (concat(' hmac:v1:', repeat('a', 64)), 0),
      (concat('hmac:v1:', repeat('a', 64), '\n'), 0),
      (concat('xhmac:v1:', repeat('a', 64)), 0),
      ('user@example.com', 0),
      ('', 0)
    ]) AS c
  )
)
SELECT v, should_match, match(v, '{{ chargeback_seed_actor_regex() }}') AS matched
FROM cases
WHERE match(v, '{{ chargeback_seed_actor_regex() }}') != should_match
