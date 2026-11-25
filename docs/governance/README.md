# Governança do Oteru

**Status:** em implantação  
**Issue-mãe:** [#1](https://github.com/luanmorenommaciel/oteru/issues/1)  
**Decisões organizacionais:** [#11](https://github.com/luanmorenommaciel/oteru/issues/11) e [#13](https://github.com/luanmorenommaciel/oteru/issues/13)

Este documento define como decisões e responsabilidades são registradas. A decisão do Captain está em [captain.md](./captain.md), e a estrutura dos pods está em [pods.md](./pods.md).

## Papéis

| Papel | Responsabilidade | Situação atual |
| --- | --- | --- |
| Captain | Desempatar prioridades, aprovar ADRs e resolver bloqueios P0 | Vinicius Sena |
| Substituto do Captain | Exercer a autoridade durante ausência declarada | Vago; depende da #11 |
| Executor | Preparar proposta, implementação e evidências | Definido por issue |
| Revisor técnico | Verificar contrato, alternativas, testes e operação | Definido por issue |
| Revisor de segurança/compliance | Avaliar privacidade, acesso, auditoria e risco regulatório | Definido por issue |
| Aprovador | Aceitar ou rejeitar a decisão dentro do mandato do Captain | Depende da #11 |

Vinicius Sena foi definido como Captain em 03/11/2026. O substituto permanece aberto.

## Fluxo de decisão

1. O executor abre ou atualiza uma issue com problema, restrições e critérios de aceite.
2. Decisões estruturais usam o template em [`docs/adr/0000-template.md`](../adr/0000-template.md).
3. O ADR permanece `proposed` enquanto houver questão material aberta ou papéis vagos.
4. O revisor técnico e o revisor de segurança/compliance registram a análise no PR.
5. O aprovador registra o aceite e a data; somente então o ADR passa a `accepted`.
6. Mudanças incompatíveis criam um novo ADR que marca o anterior como `superseded`.

Decisões relevantes não ficam apenas em chat. O registro de autoridade é a issue; o registro técnico é o ADR; o registro da mudança é o PR.

## Prazo de resposta proposto

Estes prazos entram em vigor após a ratificação da #11:

| Prioridade | Primeira resposta | Decisão ou plano de desbloqueio |
| --- | ---: | ---: |
| P0 | 4 horas úteis | 24 horas |
| P1 | 1 dia útil | 2 dias úteis |
| P2 | 2 dias úteis | 5 dias úteis |

Quando o prazo expira, o substituto assume. Se ambos estiverem indisponíveis, a decisão fica explicitamente bloqueada; ela não é presumida por silêncio.

## Pods

A estrutura, as regras de alocação e as vagas abertas estão em [pods.md](./pods.md).

## Limite de trabalho em andamento

- Cada pod mantém no máximo uma issue P0 e uma P1 em implementação simultânea.
- Cada executor mantém no máximo dois PRs de implementação abertos.
- PR empilhada deve declarar a dependência e não pode ser aprovada antes da base.
- Quem implementa não é o único aprovador da própria mudança.
- Uma issue bloqueada por ADR não entra em implementação até a decisão ficar `accepted`.

## Matriz inicial de responsabilidade

| Item | Executor | Revisor técnico | Segurança/compliance | Aprovador | Status |
| --- | --- | --- | --- | --- | --- |
| #11 — Captain | Mantenedor da issue | — | — | Vinicius Sena | Definido |
| #13 — Pods | Vinicius Sena | Representantes dos pods | — | Vinicius Sena | Estrutura proposta; alocação pendente |
| #1 — Roadmap | Vinicius Sena | Líderes dos pods | Compliance | Vinicius Sena | Índice local criado |
| #7 — ADR-001 | A atribuir | A atribuir | A atribuir | Vinicius Sena | Proposto |
| #8 — ADR-002 | A atribuir | A atribuir | A atribuir | Vinicius Sena | Proposto |
| #9 — ADR-003 | A atribuir | A atribuir | A atribuir | Vinicius Sena | Proposto |
| #10 — ADR-004 | A atribuir | A atribuir | A atribuir | Vinicius Sena | Proposto |

## Registros relacionados

- [Roadmap e gates](./roadmap.md)
- [Índice de ADRs](../adr/README.md)
- [Plano de execução da fase](../governance-adr-development-plan-2026-11-02.md)

