# ADR-001 — Superfície mínima OTLP/MCP

- **Status:** Proposed
- **Data:** 2026-11-02
- **Issue:** [#7](https://github.com/luanmorenommaciel/oteru/issues/7)
- **Issues relacionadas:** [#22](https://github.com/luanmorenommaciel/oteru/issues/22), [#42](https://github.com/luanmorenommaciel/oteru/issues/42)
- **Decidor:** Vinicius Sena, Captain
- **Revisores:** a definir na #13

## Contexto

O Oteru já recebe OTLP por HTTP e gRPC e preserva payloads protobuf. A consulta de atenção classifica um span como atividade de agente somente quando existe `gen_ai.operation.name` ou `mcp.method.name`. Isso demonstra uma base independente do fornecedor, mas ainda não define um contrato público de integração.

A #7 registra três posições: contrato forte, superfície mínima OTLP/MCP e uso de “agnóstico” apenas como claim. Sem decisão, implementações podem aceitar envelopes OTLP enquanto divergem semanticamente.

## Decisão proposta

“Agnóstico” será uma propriedade de design verificável em duas camadas:

1. **Transporte:** OTLP padrão por HTTP/protobuf ou gRPC, sem protocolo proprietário no núcleo.
2. **Semântica:** atributos `gen_ai.*` e `mcp.*` formam a superfície mínima para reconhecer sessões, operações e ferramentas.

Adapters específicos de fornecedor podem existir na borda para converter eventos em OTLP. Eles não podem alterar o contrato interno, as consultas ou o modelo de evidência.

## Contrato v1

### Obrigatório para qualquer span classificado como atividade de agente

- `service.name` no recurso;
- `trace_id`, `span_id`, início e fim válidos;
- pelo menos um de `gen_ai.operation.name` ou `mcp.method.name`;
- um identificador de sessão em `gen_ai.conversation.id` ou `conversation.id` quando o emissor possuir esse conceito;
- status OTLP e `error.type` quando houver falha.

### Obrigatório quando a operação é uma ferramenta

- `gen_ai.tool.name` ou método MCP equivalente;
- `gen_ai.tool.call.id` quando existir correlação de chamada;
- vínculo pai/filho ou trace comum entre solicitação, execução e resultado.

### Proveniência

- tráfego gerado pelo Oteru usa `oteru.traffic=synthetic`;
- ausência do marcador não prova que o tráfego é real;
- origem, versão do emissor e versão do schema devem permanecer consultáveis.

### Dados fora da superfície

OTLP válido fora da superfície mínima pode ser armazenado e consultado como telemetria genérica, mas não é promovido automaticamente a sessão ou atividade de agente.

## Invariantes para validação

1. Duas fixtures de stacks diferentes produzem o mesmo modelo canônico sem branch por fornecedor.
2. Span sem `gen_ai.operation.name` e sem `mcp.method.name` não vira sessão de agente.
3. Atributos desconhecidos não quebram ingestão nem substituem campos obrigatórios.
4. Logs, métricas e traces mantêm correlação quando os IDs são válidos.
5. Dados sintéticos ficam distinguíveis nas consultas de produto.

## Alternativas consideradas

### Claim sem contrato

Rejeitada na proposta porque não permite testar interoperabilidade nem impedir divergência entre ingestors.

### Adapter de framework no núcleo

Rejeitada na proposta porque acopla o modelo de produto a SDKs e fornecedores. Conversores de borda continuam permitidos.

### Apenas envelope OTLP

Rejeitada na proposta porque compatibilidade de transporte não garante significado comum para agentes, sessões e ferramentas.

## Consequências

- Novos emissores precisam mapear sua semântica para a superfície mínima.
- Sinais OTLP genéricos continuam disponíveis, mas não recebem inferências de produto sem evidência.
- Mudanças de SemConv exigem compatibilidade versionada e fixtures antes de alterar consultas.

## Segurança e privacidade

O contrato não exige prompt, parâmetros ou resposta. Conteúdo sensível permanece omitido por padrão. Identificadores usados para correlação obedecem ao ADR-003.

## Migração e rollback

Consultas devem aceitar os aliases documentados durante uma janela de migração. Rollback restaura a versão anterior da consulta sem reescrever telemetria bruta.

## Evidências para ratificação

- [x] Transporte OTLP HTTP/gRPC existente no repositório.
- [x] Consulta atual usa `gen_ai.operation.name`/`mcp.method.name`.
- [ ] Fixture de segundo stack além de Claude Code.
- [ ] Checker explícito de conformidade v1.
- [ ] Revisão técnica e de privacidade.
- [ ] Aceite do aprovador.

