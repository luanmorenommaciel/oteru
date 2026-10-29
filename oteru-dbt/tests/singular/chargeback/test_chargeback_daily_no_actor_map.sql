-- #46: actor_map NUNCA entra na tabela permanente. Falha se existir qualquer
-- linha attribution_source='actor_map' em fct_cost_chargeback_daily.
-- Prova real com --vars '{chargeback_fixtures: true, chargeback_service_map_seed:
-- chargeback_service_map_fixture_empty, chargeback_actor_map_seed:
-- chargeback_actor_map_fixture}' (actor_map populado com pseudônimo de teste).
SELECT day_utc, team, project, owner_team, attribution_source, cost_usd_micros
FROM {{ ref('fct_cost_chargeback_daily') }}
WHERE attribution_source = 'actor_map'
