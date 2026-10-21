# Plano #46: Chargeback de custo

Base `27ba4c3`. Toda prova vai em `docs/intents/proof/` com sufixo do SHA testado; zero testes pulados.

| # | Tarefa | Owner | Depende de | Prova |
|---|---|---|---|---|
| 1 | Aprovar intent/spec (decisões 1–3, open questions) — **concluído 2026-10-21** | Vinícius | — | decisões registradas (spec §8) |
| 2 | Criar seeds `chargeback_service_map.csv` (com `claude-code` de exemplo) e `chargeback_actor_map.csv` (só header) + `schema.yml` com testes unique/not_null/regex | Modelo | 1 | `dbt seed` + `dbt test -s seeds` PASS |
| 3 | Regra gitleaks/CI barrando `@` em `seeds/chargeback_*` (nenhum e-mail/identificador em claro) | Alicerce (revisão: Sentinela, que não edita código) | 2 | saída gitleaks (caso positivo bloqueado + negativo passa) + teste PII PASS |
| 3a | Teste dbt `test_chargeback_actor_map_hmac_version` (warn; error com `chargeback_strict_hmac_version`) | Modelo | 2 | fixture `hmac:v2:` dispara; seed só-header PASS |
| 3b | Métricas em `audit_cost_runs`: `actor_map_hmac_version_mismatch_rows` e `unassigned_share_by_source` (escrita) | Modelo | 4 | linhas em `audit_cost_runs` após run |
| 3c | Critério de alerta (sem código Vigia): mismatch > 0; share `unassigned` por `attribution_source` sobe > 5 p.p. vs mediana 7d | Vigia | 3b | critério documentado + simulação com fixture |
| 4 | Modelo `int_cost_attributed` (precedência service_map → actor_map → `unassigned`) | Modelo | 2 | `dbt build -s int_cost_attributed` PASS |
| 5 | `fct_cost_chargeback_daily` (permanente, sem actor_id); `actor_map` somado em `unassigned` | Modelo | 4 | `dbt build`, `system.tables` sem TTL |
| 6 | `fct_cost_chargeback_actor_daily` (TTL 90d via `actor_cost_ttl_days`) | Modelo | 4 | `system.tables` `engine_full`/`create_table_query` com TTL 90d |
| 7 | Testes singulares: reconciliação exata em micros por dia (vs `fct_cost_daily` e `fct_cost_actor_daily`), `test_chargeback_daily_no_actor_map` (fixture com `actor_map` populado), PII (`@`, regex hmac), accepted_values `attribution_source` | Modelo | 5, 6 | `dbt build` `SKIP=0 ERROR=0` |
| 8 | Estender/confirmar `check_cost_retention.sh` (guardrail 5) cobre `fct_cost_chargeback*` | Alicerce | 5, 6 | `check-cost-retention-46-<sha>.txt` PASS |
| 9 | E2E com fixture de #45: 682.950 micros, cenários seed vazio / mapeado, idempotência | Modelo | 7, 8 | `reconciliation-46-<sha>.txt`, `idempotency-46-<sha>.txt` |
| 10 | (Fase 2) Emitter lê `oteru.team`/`oteru.project` de `OTEL_RESOURCE_ATTRIBUTES` (sem mudar hooks; collector pass-through); `privacy/policy.json` allowlist com slug `^[a-z0-9][a-z0-9-]{0,62}$`; inválido → descarta só o atributo | Duto (revisão Sentinela) | — (fase 2 aprovada 2026-10-21) | 4 testes: válido passa; `@` removido e registro mantido; ausente → `unassigned`; inválido → mesma contagem e `sum(cost_usd_micros)` do envio sem atributo |
| 10a | (Fase 2) Comando admin self-hmac: chave só de `OTERU_PII_HMAC_KEY`, e-mail só via stdin, falha fechada (ausente/curta), imprime só `hmac:v1:<hex>`, não loga nada; chave nunca no repo/CI | Duto | 1 | execução restrita ao Vinícius e aos administradores do collector (restrição documentada; chave nunca no repo/CI); testes: vetor estilo RFC 4231; sem saída na falha; e-mail/chave ausentes de logs/histórico |
| 11 | (Fase 2) Propagar atributos (`int_api_request_dedup` aditivo, aprovado 2026-10-21) e ativar precedência `otel_attr` | Modelo | 10 | `dbt build` + reconciliação inalterada |
| 12 | Revisão final de privacidade e TTL | Sentinela | 9 (e 11) | checklist assinado na #46 |

Regras: nada de push/merge sem OK; nenhum modelo existente muda na fase 1; qualquer item marcado `breaking` exige OK do Vinícius.

## Status e decisões (2026-10-21)

- Decisões do Vinícius (2026-10-21): (a) **Fase 2 aprovada**, incluindo a mudança aditiva em `int_api_request_dedup` (novas colunas para `oteru.team`/`oteru.project`); (b) o comando admin self-hmac só pode ser executado **pelo Vinícius e pelos administradores do collector**.
- Status: plano concluído; em construção. Fase 1 → Modelo; fase 2 + comando self-hmac → Duto; regra gitleaks → Alicerce; alertas → Vigia; Sentinela revisa o PR quando a feature estiver pronta.
