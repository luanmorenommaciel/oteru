-- #46: PII. (1) Nenhuma coluna String dos novos modelos/seeds contém '@'.
-- (2) Todo actor_id dos modelos casa ^hmac:v1:[0-9a-f]{64}$.
-- (3) Todo actor_id dos seeds casa ^hmac:v[0-9]+:[0-9a-f]{64}$ (formato HMAC
--     obrigatório; versão != v1 é tratada por test_chargeback_actor_map_hmac_version).
-- Não substitui nem afrouxa tests/singular/test_no_pii.sql.
{% set sm = ref(var('chargeback_service_map_seed', 'chargeback_service_map')) %}
{% set am = ref(var('chargeback_actor_map_seed', 'chargeback_actor_map')) %}
WITH strings AS (
  SELECT 'int_cost_attributed' AS obj, arrayJoin([dedup_key, actor_id, service_name, team, project, owner_team, toString(attribution_source)]) AS v
  FROM {{ ref('int_cost_attributed') }}
  UNION ALL
  SELECT 'fct_cost_chargeback_daily', arrayJoin([team, project, owner_team, toString(attribution_source)])
  FROM {{ ref('fct_cost_chargeback_daily') }}
  UNION ALL
  SELECT 'fct_cost_chargeback_actor_daily', arrayJoin([team, project, actor_id])
  FROM {{ ref('fct_cost_chargeback_actor_daily') }}
  UNION ALL
  SELECT 'audit_cost_runs_chargeback', arrayJoin([run_id, toString(metric_name), toString(attribution_source)])
  FROM {{ ref('audit_cost_runs_chargeback') }}
  UNION ALL
  SELECT 'chargeback_service_map', arrayJoin([service_name, team, project, owner_team]) FROM {{ ref('chargeback_service_map') }}
  UNION ALL
  SELECT 'chargeback_actor_map', arrayJoin([actor_id, team, project]) FROM {{ ref('chargeback_actor_map') }}
  UNION ALL
  SELECT 'service_map_ativo', arrayJoin([service_name, team, project, owner_team]) FROM {{ sm }}
  UNION ALL
  SELECT 'actor_map_ativo', arrayJoin([actor_id, team, project]) FROM {{ am }}
),
model_actors AS (
  SELECT 'int_cost_attributed' AS obj, actor_id FROM {{ ref('int_cost_attributed') }}
  UNION ALL
  SELECT 'fct_cost_chargeback_actor_daily', actor_id FROM {{ ref('fct_cost_chargeback_actor_daily') }}
),
seed_actors AS (
  SELECT 'chargeback_actor_map' AS obj, actor_id FROM {{ ref('chargeback_actor_map') }}
  UNION ALL
  SELECT 'actor_map_ativo', actor_id FROM {{ am }}
)
SELECT obj, 'contém @' AS issue, v AS value FROM strings WHERE position(v, '@') > 0
UNION ALL
SELECT obj, 'actor_id fora de ^hmac:v1:[0-9a-f]{64}$', actor_id FROM model_actors
WHERE NOT match(actor_id, '^hmac:v1:[0-9a-f]{64}$')
UNION ALL
SELECT obj, 'actor_id de seed fora de ^hmac:v[0-9]+:[0-9a-f]{64}$', actor_id FROM seed_actors
WHERE NOT match(actor_id, '^hmac:v[0-9]+:[0-9a-f]{64}$')
