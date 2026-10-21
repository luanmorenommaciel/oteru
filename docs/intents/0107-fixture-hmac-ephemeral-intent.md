# Intent #107: Pseudônimo de fixture com HMAC de chave efêmera

Issue: [#107](https://github.com/luanmorenommaciel/oteru/issues/107) — "follow-up(#46): pseudônimo de fixture com HMAC de chave efêmera em vez de sha256 sem chave". Follow-up da revisão da #46 ([#106](https://github.com/luanmorenommaciel/oteru/pull/106)). Sem prioridade por enquanto. Base: `origin/feat/46-cost-chargeback` @ `e1b4f54`.

## Problema
Hoje `oteru-dbt/tests/load_shifted_cost_fixture.py` e os CSVs em `oteru-dbt/seeds/chargeback_fixtures/` usam como `actor_id` o valor

```
sha256("oteru-pii:v1|user.email|" + "user@example.com")
```

com o prefixo `hmac:v1:`. É um **sha256 sem chave** sobre um e-mail sintético (RFC 2606). Não há PII nem secret, mas **não é um HMAC real** e o collector (`HMAC-SHA256` + `OTERU_PII_HMAC_KEY`) nunca produziria esse valor. A Sentinela aceitou isso na #46 como ressalva documentada no README das fixtures e num comentário no loader (`e1b4f54`).

O CSV estático `hmac:v1:dd5cefde…ad14` não consegue bater com uma chave criada em cada execução: se o loader passar a HMAC de verdade, o seed commitado deixa de casar.

## Resultado desejado
1. Pseudônimos de fixture calculados com **HMAC-SHA256 de verdade**, mensagem `oteru-pii:v1|<field>|<value>` (campo `user.email`), formato `hmac:v1:<64hex>`, chave efêmera `secrets.token_bytes(32)` gerada **em cada execução** (mesmo padrão do `a2c5669`).
2. Seeds de fixture **e** linhas do loader gerados no próprio teste com a **mesma** chave, num diretório temporário **fora do repo**.
3. A linha de rotação `hmac:v2:` (64 zeros) continua válida — não é derivada da chave e não precisa casar com o loader.
4. A chave **nunca** aparece em arquivo, log, evidência, argv, env persistido ou histórico. Um teste prova isso.
5. A regra de seeds (`^hmac:v[0-9]+:[0-9a-f]{64}$` nos fixtures) continua passando; o seed de produção (`chargeback_actor_map.csv`) continua só-header, sem fixture.
6. `dbt build` e a reconciliação **682950 = 682950** continuam passando. README das fixtures e comentário do loader atualizados.
7. Nenhum `sha256` sem chave sobra **fora** de `evidence/` (histórico de prova da #46 permanece).
8. Sem backfill e sem `--full-refresh` das tabelas permanentes de produção.

## Fora de escopo
- Produção / `OTERU_PII_HMAC_KEY` real; o comando admin `hmac_lookup` (#46 fase 2) não muda.
- Backfill, `--full-refresh`, ou reescrever fatos históricos de chargeback (precisa OK do Vinícius; esta issue não pede).
- Qualquer reversão de `actor_id` ou mudança de grão/contrato dos modelos #46.
- Alertas da Vigia, regra gitleaks nova (Alicerce só se o seed guard precisar de ajuste pontual).

## Restrições herdadas (fatos do repo)
- Seed guard (`scripts/check_chargeback_seeds.py`): nenhum `@` em arquivos rastreados `**/seeds/chargeback_*`; em `seeds/chargeback_fixtures/` o `actor_id` casa `^hmac:v[0-9]+:[0-9a-f]{64}$`; produção só `hmac:v1:`.
- Templates **com** `@` não podem viver sob `seeds/chargeback_*`.
- `chargeback_fixtures` default `false`; `test_chargeback_default_no_fixture_rows` garante zero linhas de fixture no build padrão.
- Lookback 48h; fatos incrementais `delete+insert` por `day_utc`. Mudança de seed só reprocessa a janela; dias anteriores ficam congelados.
- Collector: HMAC-SHA256, mensagem `oteru-pii:v1|user.email|<email>`, saída `hmac:v1:<64 lowercase hex>`, chave ≥ 32 bytes (`hmac_lookup.actor_id` / `hmacconverter`).

## Donos
- **Duto:** loader (`load_shifted_cost_fixture.py`) e gerador de HMAC efêmero + teste de vazamento da chave.
- **Modelo:** seeds e fixtures dbt; **precisa dar OK** na parte de dbt (como o teste aponta o seed gerado) antes de implementar.
- Revisão: Sentinela. Sem prioridade (issue).

## Status e decisões (2026-10-21)

- Status: plano proposto; **sem código nesta etapa**. Aguardando OK do Modelo na opção de seed-path (§ spec 1) antes da implementação.
- Relacionada: #46 (chargeback), #106 (PR da fase 1).

Detalhes: `0107-fixture-hmac-ephemeral-spec.md`; execução: `0107-fixture-hmac-ephemeral-plan.md`.
