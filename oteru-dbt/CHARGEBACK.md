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

## Mudança de mapeamento e reprocessamento (condição Sentinela 1)

Os fatos `fct_cost_chargeback_daily` e `fct_cost_chargeback_actor_daily` são incrementais
(`delete+insert` por `day_utc`) e só reprocessam a janela de `cost_lookback_hours` (48h).
Ao mudar `chargeback_service_map` / `chargeback_actor_map`:

- dias dentro da janela de 48h são recalculados com o mapeamento novo (linhas antigas do dia são substituídas, sem duplicar);
- **dias anteriores mantêm o mapeamento antigo** (ficam congelados);
- reescrever o histórico exige `dbt build --full-refresh` (ou backfill dirigido). Isso é um **backfill e precisa do OK do Vinícius** antes de rodar fora do ambiente local.

`int_cost_attributed` é view e sempre reflete o mapeamento atual; a divergência
histórica fica só nos fatos materializados. A reconciliação total por dia com
`fct_cost_daily` continua exata nos dois casos (só muda o time, não o custo).

## Fixtures e build padrão (condições Sentinela 2 e 3)

- Seeds de fixture ficam em `seeds/chargeback_fixtures/` com `enabled: false` por padrão:
  o build padrão não cria essas tabelas nem lê esses dados.
  `test_chargeback_default_no_fixture_rows` falha se aparecer qualquer marcador de fixture
  (`time-fixture`, `projeto-fixture`, `hmac:v2:`, `attribution_source='actor_map'`, pseudônimo de teste nos seeds).
- Origem dos pseudônimos de fixture: ver `tests/fixtures/chargeback_46/README.md`. Nenhuma chave
  real nem e-mail real; nenhum material de chave está no repositório.

## Regex de PII (condição Sentinela 4)

Fonte única em `macros/chargeback_regex.sql`: seeds `^hmac:v[0-9]+:[0-9a-f]{64}$`,
modelos `^hmac:v1:[0-9a-f]{64}$`. `test_chargeback_seed_regex_cases` cobre casos
positivos e negativos (63/65 hex, maiúsculas, `vx`, prefixo/sufixo extra, e-mail, vazio).

## Auditoria sem ator (condição Sentinela 5)

`audit_cost_runs_chargeback` não tem `actor_id` nem coluna HMAC;
`test_audit_cost_runs_chargeback_no_actor` checa `system.columns` e os valores.
