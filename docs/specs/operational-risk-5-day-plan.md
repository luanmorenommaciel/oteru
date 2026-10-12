# Plano de 5 dias — Operational Risk (#21, #63–#67)

Revisão: 2026-10-12. Plano para revisão; implementação não iniciada nesta entrega.
O timebox começa após validação para implementar. Detalhamento da resposta humana:
[proposta da #65](issue-65-human-response-proposal.md).

## 1. Problema

O Oteru recebe e persiste telemetria OTLP, mas ainda não possui uma camada de
governança operacional. O collector liga receivers diretamente a exporters:
PII pode chegar ao `debug`, ao arquivo e ao ClickHouse; não há classificação de
risco, baseline, inventário autorizado nem ponto de decisão humana.

O guard atual protege apenas samples, fixtures e payloads gerados no repositório.
Ele não transforma telemetria em trânsito e não comprova scrubbing antes do
storage.

As seis issues formam três trilhas, não seis produtos independentes:

1. **Privacidade:** #21 configura a política aplicada por #67.
2. **Observação de risco:** #63, #64 e #66 normalizam sinais e classificam risco.
3. **Resposta:** #65 consolida verificações e riscos por ação pretendida para
   autorização simulada; observações posteriores abrem casos de resposta separados.

## 2. Resultado esperado ao fim do quinto dia

Um vertical slice local e reproduzível demonstra:

1. uma política versionada de PII com modos `mask` e `ignore`;
2. um canário PII enviado por OTLP que não aparece em plaintext nos destinos
   verificados depois do processor;
3. um contrato de `operational_risk_event` para permissão, inventário e anomalia;
4. uma transição conhecida de Claude Code classificada como escalada;
5. um manifesto fornecido explicitamente classificado por allowlist como
   `allowed`, `denied` ou `unknown`;
6. uma anomalia determinística contra um baseline fixo;
7. uma decisão HITL simulada por ação (`allow`, `review`, `deny`), com cobertura
   explícita das verificações, consequência calculada pela política e evidência;
   alertas posteriores seguem para casos de resposta sem conceder autorização;
8. testes, CI, documentação e roteiro de demonstração.

O resultado é uma fundação executável de governança. Não será descrito como
controle operacional completo.

## 3. Restrições

- Preservar o papel do collector como pipeline OTLP; qualquer processor precisa
  atuar antes de todos os exporters.
- Não coletar configurações pessoais do host. O inventário inicial recebe um
  manifesto explícito e redigido.
- Não adicionar credenciais, capturas reais ou chaves ao repositório.
- Começar com Claude Code e uma transição de modo canônica; suporte multiemissor
  fica fora deste timebox.
- Usar fixtures determinísticas e identidade placeholder.
- Alterações em `oteru-collector/**` precisam disparar validação no CI; hoje o
  filtro da workflow não cobre esse diretório.
- O DoD exige números e resultados verificáveis, não apenas documentação.

## 4. Fora de escopo no timebox

- Criptografia operacional de PII. Falta definir KMS/key provider, rotação,
  recuperação, nonce, autorização de decrypt e responsável pelas chaves.
- Bloquear ou pausar uma ação real. O repositório não possui executor nem canal
  de comando para MCP, plugins, skills ou agentes.
- Baseline adaptativo, sazonalidade, treinamento ou detecção genérica.
- Descoberta automática de tudo instalado na máquina do usuário.
- UI, fila durável, IdP, SLA, notificações e retry de decisões humanas reais.
  Expiração com relógio injetado e executor falso fazem parte da simulação.
- Garantia de adequação jurídica. A entrega prova mecanismo, não política legal.

## 5. Ordem e plano diário

### Dia 1 — contratos, ameaça e teste vermelho

- Consolidar #21 e #67 numa única taxonomia de PII e policy schema.
- Definir superfícies cobertas: resource attributes, record/span/datapoint
  attributes e log body.
- Definir `operational_risk_event` com origem, tipo, severidade, evidências,
  timestamps, vínculo verificável à ação quando disponível e versão da regra.
- Definir `action_request`, `check_result`, `human_gate_decision` e `response_case`;
  catálogo sintético de consequências e verificações obrigatórias por operação.
  Sugestão do detector não substitui decisão consolidada pela política.
- Medir quais sinais reais existem para `permission_mode_changed`, MCP, plugins
  e skills; registrar cobertura e ausências.
- Criar fixtures redigidas e testes que falham antes da implementação.

**Gate do dia:** contratos revisáveis, matriz de cobertura e testes vermelhos.

### Dia 2 — #21 + #67: privacidade antes do storage

- Implementar policy loader único para `mask` e `ignore`.
- Inserir o processor antes de `debug`, `file` e ClickHouse nas configurações
  suportadas.
- Falhar fechado para configuração inválida.
- Executar canários nos três sinais e em todas as superfícies definidas.

**Gate do dia:** plaintext ausente nos destinos verificados; modo e regra usados
aparecem em metadado seguro de auditoria.

### Dia 3 — #66 + #63: inventário observado e escalada

- Implementar inventário a partir de manifesto explícito e eventos observados.
- Diferenciar `installed/provided`, `observed` e `allowed`; ausência não significa
  permitido.
- Implementar regra Claude Code para uma transição de permissão acordada.
- Emitir `operational_risk_event` determinístico com evidência e versão da regra.

**Gate do dia:** matriz positive/negative/unknown passa para allowlist e
escalada; nenhum path, endpoint secreto ou credencial aparece na saída.

### Dia 4 — #64 + #65: baseline fixo e gate simulado

- Escolher uma única métrica/evento e janela pequena com baseline versionado.
- Implementar cenário normal, limite e anômalo com resultado determinístico.
- Consolidar resultados por ação e snapshot: proibição aplicável → deny;
  cobertura/contexto insuficientes → review sem aprovação possível; consequência
  moderada/alta ou regra de revisão → review; condições permissivas completas → allow.
- Calcular consequência pelo contexto estruturado; severidade sozinha não decide.
- Simular identidade por escopo, expiração, invalidação e consumo único pelo
  executor falso. Registrar razões e evidências, sem efeito externo.
- Separar casos de resposta posteriores; vencimento não significa bloqueio retroativo.

**Gate do dia:** fixtures produzem as três decisões esperadas; o artefato deixa
explícito que `deny` é recomendação/simulação, não bloqueio real.

### Dia 5 — integração, CI, documentação e demo

- Integrar OTLP → scrubbing → exportação/classificação, preservando exportação
  independente da decisão humana. Eventos posteriores alimentam casos de resposta.
- Integrar separadamente ação sintética → checks e riscos correlacionados →
  snapshot → política → revisão → revalidação → executor falso.
  Indicar quais produtores estão integrados e quais são substituídos por fixtures.
- Adicionar `oteru-collector/**` ao filtro de CI e validar configurações.
- Rodar testes, lint, PII guard e E2E local disponível.
- Documentar configuração, limites, troubleshooting e rollback.
- Preparar demonstração com evidência de entrada e saída para cada issue.
- Comparar resultado com cada DoD e registrar lacunas restantes.

**Gate final:** execução reproduzível por outra pessoa seguindo somente o README.

## 6. Definition of Done por issue

### #21 — PII configurável

**DoD do timebox**

- Policy schema versionado aceita `mask` e `ignore` por campo/padrão.
- Configuração inválida impede o collector de iniciar ou retorna erro explícito.
- A mesma taxonomia alimenta #67; não existe segundo motor de regras.
- Testes cobrem pelo menos e-mail, identidade, path de usuário e `host.name`.
- Documentação explica precedência, defaults e exemplos redigidos.

**Não concluída como feature completa:** `encrypt` permanece bloqueado até existir
ADR de gestão de chaves e teste de round trip/rotação/indisponibilidade.

### #67 — PII scrubbing antes do storage

- Processor ocorre antes de todos os exporters em cada pipeline configurado.
- Canários em logs, métricas e traces cobrem resource, record/span/datapoint
  attributes e log body.
- Busca negativa encontra **zero** ocorrências plaintext em stdout, arquivo e
  ClickHouse na execução E2E local.
- Campos não classificados permanecem intactos; payload continua OTLP válido.
- Há teste que prova que retirar o processor faz o canário reaparecer.

### #66 — inventário e allowlist

- Entrada é manifesto explícito, versionado em fixture, sem varredura do host.
- Saída distingue `provided`, `observed`, `allowed`, `denied` e `unknown`.
- Identificador inclui tipo, nome e versão quando disponível.
- Unknown segue política declarada e nunca é tratado silenciosamente como
  allowed.
- Testes cobrem MCP, plugin e skill com allowed/denied/unknown.

### #63 — alerta de escalada de permissão

- Contrato documenta qual evento e qual transição Claude Code são suportados.
- Fixture positiva gera exatamente um evento de risco com severidade e evidência.
- Fixture negativa gera zero alertas.
- Evento ausente ou desconhecido resulta em `unknown`, não em estado seguro.
- O destino do alerta é verificável localmente; falha de entrega fica visível.

### #64 — anomalia contra baseline

- Uma métrica/evento, janela, baseline fixo e threshold estão versionados.
- Casos normal, limite e anômalo têm resultados numéricos esperados.
- Baseline ausente/inválido falha explicitamente.
- Saída inclui valor observado, esperado, desvio, regra e versão.
- Documentação declara que o baseline não é adaptativo nem válido para produção.

### #65 — gate humano proporcional

- Política versionada decide por ação/revisão/snapshot, calculando consequência
  pelo contexto. Riscos conflitantes não geram autorizações independentes.
- Três ações canônicas produzem exatamente uma decisão inicial de cada tipo.
- Cobertura exige resultado explícito de cada check obrigatório; ausência de
  alerta não é pass. Cobertura incompleta impede aprovação humana.
- Proibição aplicável prevalece; alerta critical sozinho não proíbe toda ação.
- `review` exige identidade placeholder com escopo, justificativa e timestamp;
  autoaprovação e aprovação de outra revisão são recusadas.
- Prazo de 300 s é parâmetro de teste. Expiração significa autorização ausente,
  nunca rejeição atribuída ao humano; caso posterior expirado fica sem resolução.
- Mudança de parâmetros/política/evidência invalida aprovação; executor falso
  revalida validade e não consome duas vezes a mesma decisão em um run.
- Auditoria reconstrói snapshot e transições; falha de escrita não confirma decisão.
- Toda decisão declara `enforcement=simulated`; zero chamadas a executor real.
- Cumprir a matriz de fronteiras da [proposta da #65](issue-65-human-response-proposal.md),
  incluindo conflito, cobertura, identidade, tempo, replay, privacidade e fluxo posterior.

## 7. Verificação final

- `make pii-guard`: zero violações.
- `make test`: suíte completa verde.
- `make lint`: zero erros.
- Validação de todas as configurações do collector: zero configs inválidas.
- E2E de PII: zero ocorrências do canário depois do processor nos destinos
  suportados.
- E2E de risco: contagens esperadas de alerta, anomalia e decisão batem
  exatamente com a fixture.
- CI dispara quando arquivo sob `oteru-collector/**` muda.
- Uma pessoa que não implementou consegue executar a demo seguindo o README.

## 8. Decisões em aberto

- ⚠️ **Encrypt:** escolher key provider, rotação, recuperação, autorização e
  owner. Trava somente o fechamento completo da #21.
- ⚠️ **Escalada canônica:** produto precisa confirmar quais transições são risco
  e sua severidade. Trava a regra final da #63.
- ⚠️ **Métrica do baseline:** produto/operação precisa escolher qual sinal tem
  valor para o primeiro detector. Trava a regra da #64.
- ⚠️ **Política por ação:** produto/segurança definem catálogo de consequências,
  regras aplicáveis e checks obrigatórios. Trava semântica real da #65.
- ⚠️ **Contrato de cobertura:** responsáveis por #63/#64/#66 definem correlação,
  validade e integridade dos resultados. Trava integração real com a #65.
- ⚠️ **Aprovador e prazo:** operação define papéis, autenticação, prazos e
  escalonamento. 300 s é somente fixture, não SLA de operação.
- ⚠️ **Executor e auditoria:** arquitetura/segurança definem interceptação,
  atomicidade/revalidação, recuperação, retenção e ACL. Trava enforcement real.
- ⚠️ **Fonte autoritativa do inventário:** segurança precisa definir onde vive a
  allowlist e quem a mantém. Trava produção da #66, não o protótipo por fixture.

## 9. Critério de compromisso

Em cinco dias, o compromisso realista é entregar o vertical slice e o DoD do
timebox acima. #67 pode alcançar DoD funcional no ambiente local. #21, #63,
#64, #65 e #66 avançam de backlog para contrato + mecanismo demonstrável, mas
não devem ser fechadas como features operacionais enquanto suas decisões em
aberto e integrações externas permanecerem pendentes.
