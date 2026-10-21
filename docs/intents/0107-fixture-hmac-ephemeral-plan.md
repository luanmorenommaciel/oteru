# Plano #107: HMAC efêmero nas fixtures de chargeback

Base `e1b4f54` (`origin/feat/46-cost-chargeback`). Toda prova vai em `docs/intents/proof/` com sufixo do SHA testado; zero testes pulados. Sem backfill e sem `--full-refresh` de produção. Issue [#107](https://github.com/luanmorenommaciel/oteru/issues/107); follow-up da #46 / [#106](https://github.com/luanmorenommaciel/oteru/pull/106).

| # | Tarefa | Owner | Depende de | Prova |
|---|---|---|---|---|
| 1 | Aprovar intent/spec (opção de seed-path vs `--vars`, gabarito zeros vs header-only) | Modelo (OK dbt); Vinícius se discordar da opção | — | decisão na spec §9 |
| 2 | Gerador: `secrets.token_bytes(32)`, HMAC-SHA256 `oteru-pii:v1\|user.email\|user@example.com`, CSVs no `TemporaryDirectory` fora do repo, linha `hmac:v2:` inalterada | Duto | 1 | `ephemeral-hmac-107-<sha>.txt`; digest casa `^hmac:v1:[0-9a-f]{64}$` |
| 3 | Loader `pseudonymize(email, key)` com a mesma primitiva do collector (`hmac_lookup.actor_id` / `hmac.new`); chave só in-memory; atualizar comentário | Duto | 2 | loader e CSV gerado iguais na mesma run |
| 4 | Teste de vazamento: chave ausente de arquivo, log, evidência, argv, env persistido, histórico | Duto | 3 | `key-leak-107-<sha>.txt` PASS |
| 5 | dbt: `seed-paths` extra (`OTERU_EPHEMERAL_SEED_DIR`) **ou** fallback `--vars`; gabarito rastreado sem sha256 sem chave; `test_chargeback_default_no_fixture_rows` sem `dd5cefde`; produção só-header | Modelo | 1 | `seed-guard-107-<sha>.txt`; `test_chargeback_default_no_fixture_rows` PASS |
| 6 | README `tests/fixtures/chargeback_46/`; `CHARGEBACK.md`; adendo spec #46 aponta #107; apagar sha256 sem chave de `run_chargeback_46_evidence.sh` e de `test_view_pseudonymizes_emails` | Duto (README/loader/emitter); Modelo (CHARGEBACK/dbt) | 3, 5 | grep §5 da spec: zero sha256 de pseudônimo fora de `evidence/` |
| 7 | `dbt build` padrão + reconciliação 682950 e cenários A/B/C (join B com digest efêmero; v2 warn) em ClickHouse local descartável, **sem** `--full-refresh` de produção | Modelo | 5, 3 | `dbt-build-107-<sha>.txt`, `reconciliation-107-<sha>.txt` |
| 8 | gitleaks 8.24.3 no delta; seed guard; nenhum `@` em `seeds/chargeback_*` | Duto + Modelo (Alicerce só se o guard precisar de ajuste) | 6, 7 | gitleaks PASS; `check_chargeback_seeds.py` PASS |
| 9 | Revisão de privacidade (chave efêmera, tmp fora do repo, v2 placeholder) | Sentinela | 4, 7, 8 | checklist na #107 |

Regras: nada de push/merge da implementação sem OK do Modelo na parte dbt (tarefa 1); nenhum contrato #46 muda; `--full-refresh` / backfill de fatos permanentes exige OK do Vinícius e **não faz parte desta issue**.

## Status e decisões (2026-10-21)

- Status: plano proposto; este commit é **somente docs** (`0107-fixture-hmac-ephemeral-{intent,spec,plan}.md`).
- Opção registrada na spec §1: gerador + tmp fora do repo + `seed-paths` extra (path, não chave). Fallback `--vars` com o digest se o Modelo recusar.
- Donos: Duto (loader, gerador, teste de vazamento); Modelo (seeds/fixtures dbt, OK bloqueante).
- Sem prioridade (texto da issue).
