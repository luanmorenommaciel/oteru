# Spec #107: HMAC efêmero nas fixtures de chargeback

Base: `origin/feat/46-cost-chargeback` @ `e1b4f54fc049a8c6ccba5d6df9360f26d5a95049`. Engine: ClickHouse + dbt-clickhouse. Sem mudança de contrato dos modelos #46.

## 0. O que existe hoje (lido no repo)
- Loader `oteru-dbt/tests/load_shifted_cost_fixture.py` → `pseudonymize()` faz `hashlib.sha256(f"oteru-pii:v1|user.email|{email}")` **sem chave** e prefixa `hmac:v1:`. Comentário em `e1b4f54` declara que não é HMAC real.
- `oteru-dbt/seeds/chargeback_fixtures/chargeback_actor_map_fixture.csv` (e a linha v1 do `_v2.csv`) contém o digest estático `hmac:v1:dd5cefde…ad14`, casando com esse sha256 de `user@example.com`.
- `chargeback_actor_map_fixture_v2.csv` adiciona `hmac:v2:` + 64 zeros (rotação; não corresponde a ninguém).
- README `oteru-dbt/tests/fixtures/chargeback_46/README.md` e o adendo da spec #46 documentam o sha256 sem chave.
- `oteru-dbt/tests/run_chargeback_46_evidence.sh` **imprime** o sha256 sem chave (linha a remover).
- `test_chargeback_default_no_fixture_rows.sql` hardcoda o digest `dd5cefde…`.
- `oteru-emitter/tests/test_api_request_fixture.py::test_view_pseudonymizes_emails` também calcula sha256 sem chave (não é o collector).
- Collector real: `hmac.new(key, b"oteru-pii:v1|user.email|" + email, sha256)` → `hmac:v1:<hex>` (`privacy/hmac_lookup.py`, `hmacconverter`). Testes de collector já usam chave de runtime (`secrets.token_hex` / `token_bytes`); esta issue alinha as fixtures dbt a esse padrão, com `secrets.token_bytes(32)`.
- Seed de produção `seeds/chargeback_actor_map.csv`: só header. `var('chargeback_fixtures', false)`.

## 1. Como gerar seeds e loader com a mesma chave

**Decisão (opção escolhida): gerador de teste + templates sem `@` + diretório temporário fora do repo.**

Por execução de teste/evidência:

1. `key = secrets.token_bytes(32)` só em memória (nunca hex-logado, nunca escrito).
2. `actor_id = "hmac:v1:" + HMAC-SHA256(key, b"oteru-pii:v1|user.email|user@example.com")` — mesma mensagem e formato do collector. O e-mail sintético vive **só no gerador Python** (já usado em `factories.py`); não entra em CSV rastreado.
3. O gerador (Duto) escreve, num `tempfile.TemporaryDirectory()` **fora do working tree**:
   - `chargeback_actor_map_fixture.csv` — uma linha `hmac:v1:<64hex>,time-fixture,projeto-fixture`;
   - `chargeback_actor_map_fixture_v2.csv` — a mesma linha v1 **mais** `hmac:v2:` + 64 zeros (rotação inalterada).
4. O loader recebe `key: bytes` como argumento de função (in-process). Sem argv, sem env persistido, sem arquivo de chave. Subprocesso do loader, se houver, lê a chave de um pipe/anônimo ou chama o módulo; **nunca** `sys.argv`.
5. dbt aponta para esses CSVs (Modelo): em `dbt_project.yml`, o path do **gabarito** é um `seed-paths` próprio com `env_var('OTERU_EPHEMERAL_SEED_DIR', '<dir padrão do gabarito>')`. Quando a env está definida (só o path do tmp, nunca a chave), esse entry **troca** o diretório do gabarito pelo efêmero — **não soma os dois**. Motivo: dois seeds com o mesmo nome (`chargeback_actor_map_fixture`, `_v2`) quebram o dbt. O path de produção (`seeds/`, service_map + actor_map só-header) permanece. `--vars '{chargeback_fixtures: true, chargeback_actor_map_seed: …}'` como hoje.
6. **Sem a env**, o build padrão fica **exatamente como hoje**, inclusive `test_chargeback_default_no_fixture_rows` (zero fixture).
7. CSVs **rastreados** no dir padrão do gabarito deixam de carregar o sha256 sem chave. Ficam como **gabarito de forma**: `hmac:v1:` + 64 zeros e, no v2, a linha `hmac:v2:` + 64 zeros. Continuam a casar com a macro `chargeback_seed_actor_regex` (`^hmac:v[0-9]+:[0-9a-f]{64}$`); o teste de PII **não é afrouxado**. Não são o join do loader. Testes de join usam o tmp.

A linha `hmac:v2:` **não** é HMAC da chave efêmera; continua placeholder de rotação.

### 1.1 CI artifacts e `git ls-files` (Alicerce)
O `TemporaryDirectory` do gerador e o `oteru-dbt/target/` **nunca** sobem como artifact de CI (nenhum job `actions/upload-artifact`, equivalente, ou `dbt docs generate` que empacote esses paths). O tmp vive fora do working tree; `git ls-files` (e o seed guard, que já usa `git ls-files`) **não** o lista — nem durante o job, nem depois. `.gitignore` de `target/` permanece; nenhum workflow passa `--include-untracked` / path do tmp para upload. Prova: dump dos steps de artifact do workflow + `git ls-files` vazio para o path do tmp.

### 1.2 Isolamento dos gabaritos `hmac:*:000…` (Vigia)
Os CSVs de gabarito com `hmac:v1:` + 64 zeros e `hmac:v2:` + 64 zeros **só** podem ser lidos pelo `seed-paths` de **teste** (dir padrão do gabarito, **sem** `OTERU_EPHEMERAL_SEED_DIR`). Com a env definida, esse path é **substituído** pelo tmp (HMAC real); gabarito e efêmero nunca entram juntos. Fora do teste, o `actor_map` de produção (`chargeback_actor_map`, build padrão, `chargeback_fixtures=false`) **não** lê esses arquivos e **não** contém nenhuma linha `hmac:vN:` só de zeros.

**Teste dbt (Modelo, critério Vigia), obrigatório — falha se:**
- o `actor_map` materializado no **build padrão** (`ref(var('chargeback_actor_map_seed', 'chargeback_actor_map'))` com fixtures desligadas) tiver qualquer linha `hmac:vN:` só de zeros.

Extensão natural de `test_chargeback_default_no_fixture_rows` (ou singular novo `test_chargeback_no_zero_hmac_placeholder`). O alerta da Vigia não muda nesta issue; o teste é o fail-closed.

**Regra no seed guard (Alicerce, dono de `scripts/check_chargeback_seeds.py`; Sentinela aprova antes):** rejeitar `actor_id` casando `^hmac:v[0-9]+:0{64}$` em qualquer seed rastreado **fora** de `seeds/chargeback_fixtures/`. Gabaritos de zeros **dentro** de `seeds/chargeback_fixtures/` continuam permitidos (regex da macro inalterada; PII não afrouxa). Um teste (positivo: zeros em produção FAIL; negativo: zeros só no gabarito PASS) entra em `tests/test_chargeback_seeds.py`. Sem aprovação da Sentinela essa regra não mergeia.

### Alternativas consideradas
- *Somar `seed-paths` (gabarito + tmp).* Dois seeds com o mesmo nome quebram o dbt. **Rejeitada** (Modelo).
- *`--vars` com o digest* (`chargeback_fixture_actor_id: hmac:v1:<hex>`) e um seed/modelo jinja de uma linha. Menos I/O; o digest (não a chave) aparece na linha de comando do dbt (`ps`, traces). Mais mudança dbt. Rejeitada como primária.
- *Sobrescrever os CSVs rastreados no working tree e restaurar.* Simples, mas suja o tree e arrisca commit. Rejeitada.
- *Chave de teste fixa commitada / env de CI.* Rejeitada: a issue exige efêmera e zero chave em arquivo/histórico.
- *HMAC com `OTERU_PII_HMAC_KEY` de produção.* Rejeitada: mistura fixture com chave real; o self-hmac admin não é para CI.
- *Deixar o CSV estático e só mudar o loader.* Impossível: o digest muda a cada `token_bytes(32)`.

## 2. Segredo da chave (fail-closed no teste)
A chave **nunca** aparece em:
- arquivo (CSV, README, evidência, `.env`, fixture commitada);
- log (stdout/stderr do loader, dbt, pytest, collector);
- evidência em `evidence/` ou `docs/intents/proof/`;
- argv (`ps`, `sys.argv`, histórico de shell);
- env persistido (não exportar `OTERU_PII_HMAC_KEY` no job; não gravar em `os.environ` além do processo de teste, e mesmo aí preferir argumento in-memory);
- `git log` / blobs.

**Teste (Duto), obrigatório:** após uma execução que gera HMAC e carrega fixture, o valor `key.hex()` / `key` **não** está em stdout, stderr, caplog, arquivos do tmp além de nenhum (CSVs só têm `hmac:v1:<digest>`), evidências geradas, `git diff`, nem na linha de comando de filhos. Assert positivo: o `actor_id` do loader **é igual** ao do CSV gerado e casa `^hmac:v1:[0-9a-f]{64}$`; um segundo `token_bytes(32)` produz digest **diferente**.

Vetor de regressão: reutilizar `hmac_lookup.actor_id` / `digest` (RFC 4231) para não divergir do collector.

## 3. Seeds, regex, produção
- Macro `chargeback_seed_actor_regex` (`^hmac:v[0-9]+:[0-9a-f]{64}$`) **não é afrouxada**; o teste de PII idem. Gabaritos de zeros continuam a casar com a macro. O seed guard (Alicerce) passa a **rejeitar** digest só de zeros **fora** de `seeds/chargeback_fixtures/` (§1.2). Produção continua `hmac:v1:` só-header.
- `test_chargeback_default_no_fixture_rows`: deixar de hardcodar `dd5cefde…`; marcadores `time-fixture` / `projeto-fixture` / `hmac:v2:` / `attribution_source='actor_map'` bastam (Modelo).
- `chargeback_service_map_fixture_empty` e o seed de produção **não** ganham linhas de fixture.
- Nenhum `@` em `seeds/chargeback_*` rastreados. E-mail sintético só no gerador.

## 4. Contratos dbt e reconciliação
Nenhuma coluna, grão, TTL ou precedência muda. `dbt build` (padrão, `chargeback_fixtures=false`) SKIP=0 ERROR=0. Com fixtures ligadas no ClickHouse local descartável: `sum(cost_usd_micros)` em `fct_cost_chargeback_daily` = **682950** = `fct_cost_daily` (mesma fixture #45). Cenários A/B/C da evidência #46 (unassigned / actor_map / hmac:v2 warn) continuam, agora com o digest efêmero no join B.

**Sem backfill e sem `--full-refresh`** nas tabelas permanentes de produção. ClickHouse local descartável pode reconstruir seeds de fixture; não se pede `--full-refresh` dos fatos `fct_cost_*` de produção nem OK de histórico ao Vinícius.

## 5. Docs e limpeza de sha256 sem chave
Atualizar (implementação, não este commit):
- `oteru-dbt/tests/fixtures/chargeback_46/README.md` — origem = HMAC-SHA256 com chave efêmera de teste; não é a chave do collector; `hmac:v2:` continua placeholder.
- Comentário de `pseudonymize()` no loader — HMAC real, chave só em memória, teste-only.
- Adendo da spec #46 / `oteru-dbt/CHARGEBACK.md` — apontar para #107; não reescrever provas antigas.
- Remover o `python3 -c 'hashlib.sha256…'` de `run_chargeback_46_evidence.sh`.
- `test_view_pseudonymizes_emails` (emitter): deixar de afirmar sha256 sem chave como se fosse o collector.

`hashlib.sha256` que **não** é pseudônimo de fixture permanece: checksum de policy em `compile.py`, ids de span em hooks. `evidence/` e `docs/intents/proof/` históricos **não** se reescrevem.

Grep de aceite: fora de `evidence/` e de `docs/intents/proof/`, nenhum `sha256("oteru-pii:v1|user.email|` / `hashlib.sha256(…user.email` usado para `hmac:v1:`.

## 6. Campos PII (inalterados)

| Campo | Classe | Onde pode existir |
|---|---|---|
| `actor_id` (`hmac:v1:` de chave efêmera) | pseudônimo de **teste** | tmp fora do repo, ClickHouse local, stdout do teste (só o digest) |
| chave `token_bytes(32)` | secret de teste | **somente** memória do processo de teste |
| `user@example.com` | e-mail sintético RFC 2606 | gerador Python / fixture OTLP já redigida; **nunca** em `seeds/chargeback_*` |
| `hmac:v1:`/`hmac:v2:` + 64 zeros | placeholder de forma (não é ator) | **somente** gabarito de teste / `seed-paths` de teste; **nunca** seed de produção nem `actor_map` do build padrão |
| `OTERU_PII_HMAC_KEY` | chave de produção | **nunca** nesta issue |

## 7. Critérios de aceite (testáveis)
1. Loader e gerador usam HMAC-SHA256, mensagem `oteru-pii:v1\|user.email\|<email>`, saída `hmac:v1:<64 lowercase hex>`, chave `secrets.token_bytes(32)` por run.
2. CSV gerado e linhas inseridas pelo loader compartilham o mesmo `actor_id` na mesma run; run seguinte (outra chave) produz digest diferente.
3. Linha `hmac:v2:` + 64 zeros permanece; teste de versão HMAC (warn / error estrito) continua PASS.
4. Teste de vazamento da chave PASS (critério §2): chave ausente de arquivo, log, evidência, argv, env persistido, histórico.
5. `scripts/check_chargeback_seeds.py` PASS, inclusive a regra Alicerce de zeros fora de `seeds/chargeback_fixtures/`; regex de fixtures `^hmac:v[0-9]+:[0-9a-f]{64}$` **sem afrouxar PII**; gabarito de zeros casa com a macro; produção sem linhas de fixture; **sem a env**, `test_chargeback_default_no_fixture_rows` PASS no build padrão (igual a hoje).
6. `dbt build` SKIP=0 ERROR=0; reconciliação `682950 = 682950` em micros vs `fct_cost_daily`.
7. README das fixtures e comentário do loader atualizados; nenhum sha256 sem chave de pseudônimo fora de `evidence/` (e proof histórico).
8. `gitleaks` 8.24.3 no delta PASS. Nenhum `@` novo em `seeds/chargeback_*`.
9. Sem `--full-refresh` / backfill de produção. Modelo deu OK na parte dbt antes do merge da implementação.
10. `git diff` da implementação não altera contratos #46 (`fct_cost_daily`, `fct_cost_actor_daily`, `int_api_request_dedup`, `stg_api_request`).
11. **(Alicerce)** Nenhum job de CI faz upload do `TemporaryDirectory` nem de `oteru-dbt/target/` como artifact; o path do tmp **nunca** entra em `git ls-files` (§1.1).
12. **(Vigia)** Gabaritos `hmac:v1:000…` / `hmac:v2:000…` só no `seed-paths` de teste (nunca somados ao efêmero). Teste dbt §1.2 FAIL se `hmac:v[0-9]+:0{64}` aparecer no `actor_map` do build padrão.
13. **(Modelo)** Com `OTERU_EPHEMERAL_SEED_DIR` definida, `seed-paths` **troca** o dir do gabarito pelo tmp (`env_var` com default no `dbt_project.yml`); sem a env, build padrão idêntico ao de hoje.
14. **(Alicerce)** `check_chargeback_seeds.py` rejeita digest só de zeros fora de `seeds/chargeback_fixtures/`, com teste; Sentinela aprova antes. Alicerce é o dono dessa regra.

## 8. Provas esperadas (em `docs/intents/proof/`, sufixo = SHA curto do commit testado)
- `dbt-build-107-<sha>.txt` — `PASS=… WARN=0 ERROR=0 SKIP=0` no build padrão.
- `reconciliation-107-<sha>.txt` — totais 682950 vs 682950, diff 0.
- `ephemeral-hmac-107-<sha>.txt` — loader e CSV gerado iguais; segundo run diferente; regex; v2 intacta.
- `key-leak-107-<sha>.txt` — saída do teste §2 (chave ausente de stdout/stderr/arquivos/argv).
- `seed-guard-107-<sha>.txt` — regra Alicerce (zeros fora de `chargeback_fixtures` FAIL; zeros só no gabarito PASS) + gitleaks no delta.
- `ci-artifacts-107-<sha>.txt` — jobs de CI sem upload de tmp/`target/`; `git ls-files` do path do tmp vazio.
- `zero-hmac-isolation-107-<sha>.txt` — teste §1.2 PASS no build padrão; FAIL deliberado se um placeholder de zeros for injetado no seed de produção.
- Não incluir a chave nem o `token_bytes` em nenhum arquivo de prova (só `hmac:v1:<hex>` e PASS/FAIL).

## 9. Open questions
1. **Modelo:** path default do gabarito no `env_var` (manter `seeds/chargeback_fixtures/` como segundo `seed-paths` e tirá-lo de baixo de `seeds/`, para o dbt não descobrir os dois). Detalhe de layout; a troca (não soma) está decidida.
2. O teste `test_view_pseudonymizes_emails` do emitter entra no pacote Duto desta issue (sim, está fora de `evidence/` e afirma sha256 sem chave).

## Status e decisões (2026-10-21)

- Status: spec proposta; **docs only**. `seed-paths` troca (não soma) decidido pelo Modelo; regra de zeros no seed guard é do Alicerce (Sentinela aprova antes).
- Donos: Duto (loader + gerador + teste de vazamento); Modelo (`seed-paths` via `env_var` com default, fixtures dbt); Alicerce (§1.1 CI artifacts **e** regra de zeros em `check_chargeback_seeds.py`); Vigia (critério §1.2 no `actor_map` do build padrão).
- Sem backfill, sem `--full-refresh` de produção, sem prioridade (texto da #107).
