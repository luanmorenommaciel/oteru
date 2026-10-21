-- #46 (Sentinela, cond. 2) + #107 §1.2: build padrão (sem chargeback_fixtures)
-- não gera nenhuma linha de fixture. Só avalia quando chargeback_fixtures=false.
-- Marcadores: team/project de fixture, prefixo hmac:v2:, attribution actor_map
-- (actor_map de produção é só header) e hmac:vN: só de zeros (#107 Vigia).
{% if not (var('chargeback_fixtures', false) | as_bool) %}
SELECT 'int_cost_attributed' AS obj, count() AS n FROM {{ ref('int_cost_attributed') }}
WHERE team IN ('time-fixture') OR project IN ('projeto-fixture') OR attribution_source = 'actor_map' OR startsWith(actor_id, 'hmac:v2:') OR match(actor_id, '^hmac:v[0-9]+:0{64}$')
HAVING n > 0
UNION ALL
SELECT 'fct_cost_chargeback_daily', count() AS n FROM {{ ref('fct_cost_chargeback_daily') }}
WHERE team = 'time-fixture' OR project = 'projeto-fixture' OR attribution_source = 'actor_map'
HAVING n > 0
UNION ALL
SELECT 'fct_cost_chargeback_actor_daily', count() AS n FROM {{ ref('fct_cost_chargeback_actor_daily') }}
WHERE team = 'time-fixture' OR project = 'projeto-fixture' OR startsWith(actor_id, 'hmac:v2:') OR match(actor_id, '^hmac:v[0-9]+:0{64}$')
HAVING n > 0
UNION ALL
SELECT 'chargeback_service_map', count() AS n FROM {{ ref('chargeback_service_map') }}
WHERE team = 'time-fixture' OR project = 'projeto-fixture'
HAVING n > 0
UNION ALL
SELECT 'chargeback_actor_map', count() AS n FROM {{ ref('chargeback_actor_map') }}
WHERE team = 'time-fixture' OR project = 'projeto-fixture' OR startsWith(actor_id, 'hmac:v2:') OR match(actor_id, '^hmac:v[0-9]+:0{64}$')
HAVING n > 0
UNION ALL
SELECT 'audit_cost_runs_chargeback (run atual)', count() AS n FROM {{ ref('audit_cost_runs_chargeback') }}
WHERE run_id = '{{ invocation_id }}'
  AND ((metric_name = 'actor_map_hmac_version_mismatch_rows' AND metric_value > 0)
    OR (metric_name = 'unassigned_share_by_source' AND attribution_source = 'actor_map' AND metric_value > 0))
HAVING n > 0
{% else %}
SELECT 'fixtures ligadas: teste não se aplica' AS obj, toUInt64(0) AS n WHERE 1 = 0
{% endif %}
