# Organização dos pods do Oteru

- **Status:** Proposed
- **Data:** 2026-11-03
- **Issue:** [#13](https://github.com/luanmorenommaciel/oteru/issues/13)
- **Aprovador:** Vinicius Sena, Captain

## Estrutura proposta

| Pod | Escopo | Issues iniciais | Gate de revisão |
| --- | --- | --- | --- |
| Fundação/Collector | Ingestão, contratos OTLP/MCP, buffer e confiabilidade | #7, #8, #6, #22, #42 | Revisor técnico |
| Dados/Compliance | PII, auditoria, retenção e Compliance Pack | #9, #5, #72, #73, #74 | Segurança/compliance |
| Produto/UI | Adoção, sessões, anti-drift e autonomia | #50–#58, #68–#71 | Produto + privacidade |
| Segurança/Qualidade | CI, validação issue × PR, Break Glass e enforcement | #10, #23, #108, #119 | Segurança + Captain |

## Regras de alocação

- Cada issue tem um executor e pelo menos um revisor diferente.
- Mudanças de segurança/compliance exigem o respectivo revisor especializado.
- Cada pod mantém no máximo uma P0 e uma P1 em implementação simultânea.
- Cada executor mantém no máximo dois PRs de implementação abertos.
- PR empilhada declara sua base e não é aprovada antes dela.
- Uma issue bloqueada por ADR não entra em implementação incompatível com a proposta vigente.

## Alocação inicial

| Item | Pod | Executor | Revisor | Aprovador |
| --- | --- | --- | --- | --- |
| #7 e #8 | Fundação/Collector | A atribuir | A atribuir | Vinicius Sena |
| #9 | Dados/Compliance | A atribuir | A atribuir | Vinicius Sena |
| #10 | Segurança/Qualidade | A atribuir | A atribuir | Vinicius Sena |

Os nomes de executor e revisor permanecem abertos para atribuição na #13. A estrutura pode ser aceita antes do preenchimento das vagas, mas a implementação de cada issue depende de sua própria alocação.

