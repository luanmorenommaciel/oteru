# Chargeback de custo (#46) — fase 1

Fonte da verdade: `docs/intents/0046-cost-chargeback-{intent,spec,plan}.md`.
Fase 1 é 100% aditiva: nenhum modelo, seed, teste ou macro existente foi alterado.

## Objetos

| Objeto | Tipo | Grão | Retenção |
|---|---|---|---|
| `seeds/chargeback_service_map.csv` | seed | `service_name` (único) | — |
| `seeds/chargeback_actor_map.csv` | seed (só header) | `actor_id` `hmac:v1:<64 hex>` (único) | — |
| `int_cost_attributed` | view, contrato | 1 linha por `dedup_key` | herda a fonte |
| `fct_cost_chargeback_daily` | incremental, contrato | `day_utc × team × project × owner_team × attribution_source` | **permanente** (sem TTL, sem `actor_id`) |
| `fct_cost_chargeback_actor_daily` | incremental, contrato | `day_utc × team × project × actor_id` | TTL `day_utc + actor_cost_ttl_days` (90) |
| `audit_cost_runs_chargeback` | incremental append, contrato | run × métrica × dia × fonte | — |

Precedência por request: `otel_attr` (fase 2) → `service_map` → `actor_map` → `unassigned`.
Custo nunca é descartado. Custo de `actor_map` vai para `unassigned` na tabela permanente.

## Variáveis

| Var | Padrão | Uso |
|---|---|---|
| `chargeback_service_map_seed` | `chargeback_service_map` | troca o seed lido (fixtures) |
| `chargeback_actor_map_seed` | `chargeback_actor_map` | troca o seed lido (fixtures) |
| `chargeback_fixtures` | `false` | habilita os seeds em `seeds/chargeback_fixtures/` |
| `chargeback_strict_hmac_version` | `false` | `test_chargeback_actor_map_hmac_version` vira `error` |

## Cenários de fixture

```bash
V='{chargeback_fixtures: true, chargeback_service_map_seed: chargeback_service_map_fixture_empty'
dbt build --profiles-dir . --vars "$V}"                                                        # 100% unassigned
dbt build --profiles-dir . --vars "$V, chargeback_actor_map_seed: chargeback_actor_map_fixture}"     # actor_map
dbt build --profiles-dir . --vars "$V, chargeback_actor_map_seed: chargeback_actor_map_fixture_v2}"  # hmac:v2 → WARN
```

Script de prova: `bash oteru-dbt/tests/run_chargeback_46_evidence.sh` (ClickHouse local descartável).

## Métricas (`audit_cost_runs_chargeback`, join com `audit_cost_runs` por `run_id`)

- `actor_map_hmac_version_mismatch_rows`: linhas do actor_map ativo sem prefixo `hmac:v1:`. Alerta Vigia se > 0.
- `unassigned_share_by_source`: por dia (últimos 8) e `attribution_source`, fração do custo do dia (0..1).
  A linha `attribution_source='unassigned'` é a participação não atribuída.

## PII

Chaves só `service_name` ou `actor_id` HMAC. Nunca e-mail, nome ou login, nem em comentários.
`test_chargeback_no_pii` falha com qualquer `@` e com `actor_id` fora do formato HMAC.
