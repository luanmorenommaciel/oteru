-- #46: rotação de chave não pode falhar em silêncio. Linhas do actor_map ativo
-- cujo prefixo difere da versão corrente (hmac:v1:). warn por padrão; error com
-- --vars '{chargeback_strict_hmac_version: true}'.
{{ config(severity=('error' if (var('chargeback_strict_hmac_version', false) | as_bool) else 'warn')) }}
SELECT actor_id, team, project
FROM {{ ref(var('chargeback_actor_map_seed', 'chargeback_actor_map')) }}
WHERE NOT startsWith(actor_id, 'hmac:v1:')
