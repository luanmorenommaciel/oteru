# Plano #107: HMAC efêmero nas fixtures de chargeback

Base `e1b4f54` (`origin/feat/46-cost-chargeback`). Toda prova vai em `docs/intents/proof/` com sufixo do SHA testado; zero testes pulados. Sem backfill e sem `--full-refresh` de produção. Issue [#107](https://github.com/luanmorenommaciel/oteru/issues/107); follow-up da #46 / [#106](https://github.com/luanmorenommaciel/oteru/pull/106).

| # | Tarefa | Owner | Depende de | Prova |
|---|---|---|---|---|
| 1 | Aprovar intent/spec (layout do path default do gabarito no `env_var`; troca já decidida) | Modelo (OK dbt); Vinícius se discordar | — | decisão na spec §9 |
| 2 | Gerador: `secrets.token_bytes(32)`, HMAC-SHA256 `oteru-pii:v1\|user.email\|user@example.com`, CSVs no `TemporaryDirectory` fora do repo, linha `hmac:v2:` inalterada | Duto | 1 | `ephemeral-hmac-107-<sha>.txt`; digest casa `^hmac:v1:[0-9a-f]{64}$` |
| 3 | Loader `pseudonymize(email, key)` com a mesma primitiva do collector (`hmac_lookup.actor_id` / `hmac.new`); chave só in-memory; atualizar comentário | Duto | 2 | loader e CSV gerado iguais na mesma run |
| 4 | Teste de vazamento: chave ausente de arquivo, log, evidência, argv, env persistido, histórico | Duto | 3 | `key-leak-107-<sha>.txt` PASS |
| 5 | dbt: `seed-paths` **troca** (não soma) o dir do gabarito via `env_var('OTERU_EPHEMERAL_SEED_DIR', default)` no `dbt_project.yml`; sem a env, build padrão idêntico (zero fixture); gabarito de zeros casa com a macro, PII não afrouxa; `test_chargeback_default_no_fixture_rows` sem `dd5cefde` | Modelo | 1 | `seed-guard-107-<sha>.txt`; build padrão = hoje |
| 6 | README `tests/fixtures/chargeback_46/`; `CHARGEBACK.md`; adendo spec #46 aponta #107; apagar sha256 sem chave de `run_chargeback_46_evidence.sh` e de `test_view_pseudonymizes_emails` | Duto (README/loader/emitter); Modelo (CHARGEBACK/dbt) | 3, 5 | grep §5 da spec: zero sha256 de pseudônimo fora de `evidence/` |
| 7 | `dbt build` padrão + reconciliação 682950 e cenários A/B/C (join B com digest efêmero; v2 warn) em ClickHouse local descartável, **sem** `--full-refresh` de produção | Modelo | 5, 3 | `dbt-build-107-<sha>.txt`, `reconciliation-107-<sha>.txt` |
| 8 | gitleaks 8.24.3 no delta; seed guard (depois da regra 8c); nenhum `@` em `seeds/chargeback_*` | Duto + Modelo | 6, 7, 8c | gitleaks PASS; `check_chargeback_seeds.py` PASS |
| 8a | CI: nenhum job faz upload do `TemporaryDirectory` nem de `oteru-dbt/target/` como artifact; tmp fora de `git ls-files` (spec §1.1) | Alicerce | 2, 5 | `ci-artifacts-107-<sha>.txt` |
| 8b | Isolar gabaritos `hmac:v1:000…` / `hmac:v2:000…` ao `seed-paths` de teste (nunca somados); teste dbt FAIL se zeros aparecerem no `actor_map` do build padrão (spec §1.2) | Modelo (critério: Vigia) | 5 | `zero-hmac-isolation-107-<sha>.txt` |
| 8c | `check_chargeback_seeds.py`: rejeitar digest só de zeros fora de `seeds/chargeback_fixtures/`, com teste; **Sentinela aprova antes** | Alicerce (dono do script) | 5 | teste em `tests/test_chargeback_seeds.py`; aprovação Sentinela |
| 9 | Revisão de privacidade (chave efêmera, tmp fora do repo, placeholders isolados, CI sem artifact de tmp/`target/`, regra de zeros do seed guard) | Sentinela | 4, 7, 8, 8a, 8b, 8c | checklist na #107 |

Regras: nada de push/merge da implementação sem OK do Modelo na parte dbt (tarefa 1) e sem aprovação da Sentinela na regra de zeros do seed guard (tarefa 8c); nenhum contrato #46 muda; `--full-refresh` / backfill de fatos permanentes exige OK do Vinícius e **não faz parte desta issue**. Nenhum `seed-paths` soma gabarito + tmp.

## Status e decisões (2026-10-21)

- Status: plano proposto; este commit é **somente docs** (`0107-fixture-hmac-ephemeral-{intent,spec,plan}.md`).
- Opção registrada na spec §1: gerador + tmp fora do repo; `seed-paths` **troca** o gabarito pelo efêmero via `env_var` com default (não soma: mesmo nome quebra o dbt). Sem a env, build padrão = hoje.
- Donos: Duto (loader, gerador, teste de vazamento); Modelo (`seed-paths` / fixtures dbt); Alicerce (CI artifacts **e** regra de zeros em `check_chargeback_seeds.py`, Sentinela aprova antes); Vigia (critério de isolamento no `actor_map` do build padrão).
- Sem prioridade (texto da issue).
