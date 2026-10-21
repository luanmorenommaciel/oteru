# Plano #46: Chargeback de custo

Base `27ba4c3`. Toda prova vai em `docs/intents/proof/` com sufixo do SHA testado; zero testes pulados.

| # | Tarefa | Owner | Depende de | Prova |
|---|---|---|---|---|
| 1 | Aprovar intent/spec (decisões 1–3, open questions) | Vinícius | — | comentário na #46 |
| 2 | Criar seeds `chargeback_service_map.csv` (com `claude-code` de exemplo) e `chargeback_actor_map.csv` (só header) + `schema.yml` com testes unique/not_null/regex | Modelo | 1 | `dbt seed` + `dbt test -s seeds` PASS |
| 3 | Revisar seeds: nenhum e-mail/identificador em claro; ajustar gitleaks/CI para barrar `@` em `seeds/chargeback_*` | Sentinela | 2 | saída gitleaks + teste PII PASS |
| 4 | Modelo `int_cost_attributed` (precedência service_map → actor_map → `unassigned`) | Modelo | 2 | `dbt build -s int_cost_attributed` PASS |
| 5 | `fct_cost_chargeback_daily` (permanente, sem actor_id) com contrato | Modelo | 4 | `dbt build`, `system.tables` sem TTL |
| 6 | `fct_cost_chargeback_actor_daily` (TTL 90d via `actor_cost_ttl_days`) | Modelo | 4 | `system.tables` `engine_full`/`create_table_query` com TTL 90d |
| 7 | Testes singulares: reconciliação exata em micros por dia (vs `fct_cost_daily` e `fct_cost_actor_daily`), PII (`@`, regex hmac), accepted_values `attribution_source` | Modelo | 5, 6 | `dbt build` `SKIP=0 ERROR=0` |
| 8 | Estender/confirmar `check_cost_retention.sh` (guardrail 5) cobre `fct_cost_chargeback*` | Alicerce | 5, 6 | `check-cost-retention-46-<sha>.txt` PASS |
| 9 | E2E com fixture de #45: 682.950 micros, cenários seed vazio / mapeado, idempotência | Modelo | 7, 8 | `reconciliation-46-<sha>.txt`, `idempotency-46-<sha>.txt` |
| 10 | (Fase 2) Resource attributes `oteru.team`/`oteru.project` no emitter e hooks; política de privacidade | Duto (+Sentinela) | open questions 1–2 | teste do emitter/hooks + amostra `otel_logs` sem `@` |
| 11 | (Fase 2) Propagar atributos (`int_api_request_dedup` aditivo, requer OK) e ativar precedência `otel_attr` | Modelo | 10, OK Vinícius | `dbt build` + reconciliação inalterada |
| 12 | Revisão final de privacidade e TTL | Sentinela | 9 (e 11) | checklist assinado na #46 |

Regras: nada de push/merge sem OK; nenhum modelo existente muda na fase 1; qualquer item marcado `breaking` exige OK do Vinícius.
