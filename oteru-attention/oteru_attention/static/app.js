const state = { csrf: "", demo: false, dashboard: null };
const $ = (id) => document.getElementById(id);

function node(tag, className = "", value = "") {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (value !== "") item.textContent = String(value);
  return item;
}

function clear(element) { element.replaceChildren(); }
function date(value) {
  if (!value) return "sem horário";
  const result = new Date(value);
  return Number.isNaN(result.getTime()) ? value : new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(result);
}
function short(value) { return value && value.length > 19 ? `${value.slice(0, 9)}…${value.slice(-6)}` : (value || "sem sessão"); }
function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) { headers["Content-Type"] = "application/json"; headers["X-Oteru-CSRF"] = state.csrf; }
  if (options.method === "DELETE") headers["X-Oteru-CSRF"] = state.csrf;
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function showEvidence(evidence) {
  try {
    const suffix = state.demo ? "?demo=1" : "";
    const detail = await api(`/api/evidence/${encodeURIComponent(evidence.id)}${suffix}`);
    const labels = [
      ["Origem", detail.source_table], ["Horário", date(detail.time)],
      ["Emissor", detail.service], ["Sessão", detail.session_id || "—"],
      ["Evento", detail.event], ["Ferramenta", detail.tool_name || "—"],
      ["Chamada", detail.tool_use_id || "—"], ["Trace ID", detail.trace_id || "—"],
      ["Span ID", detail.span_id || "—"], ["Resultado", detail.status || "—"],
      ["Origem do dado", detail.provenance]
    ];
    const grid = node("dl", "evidence-grid");
    labels.forEach(([label, value]) => { grid.append(node("dt", "", label), node("dd", "", value)); });
    const content = $("evidence-content"); clear(content);
    content.append(grid, node("p", "evidence-foot", "Esta referência aponta para um registro da tabela indicada. Conteúdo de prompt, identidade e saída de ferramentas não são exibidos nesta tela."));
    $("evidence-dialog").showModal();
  } catch (error) { notice(error.message); }
}

function evidenceButton(evidence, label = "Ver evidência ↗") {
  const button = node("button", "evidence-link", label);
  button.type = "button";
  button.addEventListener("click", () => showEvidence(evidence));
  return button;
}

function renderCoverage(coverage, queue, sessions, queueTotal, sessionsTotal) {
  $("connection").textContent = "ClickHouse conectado";
  $("connection").classList.add("ok");
  $("coverage-title").textContent = coverage.demo ? "Amostra incluída na análise" : "Dados observados";
  $("coverage-window").textContent = `Janela: ${date(coverage.from)} → ${date(coverage.to)}`;
  $("coverage-detail").textContent = `${coverage.logs} logs · ${coverage.agent_spans} spans de agente · ${coverage.services.length} emissores`;
  $("queue-count").textContent = queueTotal > queue.length ? `${queue.length} de ${queueTotal} ocorrências` : `${queueTotal} ocorrência${queueTotal === 1 ? "" : "s"}`;
  $("agent-count").textContent = sessionsTotal > sessions.length ? `${sessions.length} de ${sessionsTotal} sessões` : `${sessionsTotal} sessão${sessionsTotal === 1 ? "" : "ões"}`;
  if (coverage.demo && coverage.future_sample_records) {
    notice(`${coverage.future_sample_records} registro(s) da amostra têm horário futuro. Eles aparecem para demonstração e não contam como atividade recente.`);
  } else if (coverage.truncated) {
    notice("A consulta atingiu o limite de registros. Totais e respostas podem estar incompletos.");
  } else { notice(); }
}

function renderQueue(queue) {
  const list = $("queue-list"); clear(list);
  if (!queue.length) { list.append(node("div", "empty", "Nenhuma falha, rejeição ou mudança de permissão encontrada na janela consultada.")); return; }
  queue.forEach((item) => {
    const row = node("article", "queue-item");
    const score = node("div", `severity ${item.score < 80 ? "medium" : ""}`, item.score);
    score.title = `Prioridade ${item.score} · ${item.kind}`;
    const body = node("div");
    body.append(node("div", "item-title", item.title), node("div", "item-reason", item.reason));
    const meta = node("div", "item-meta");
    meta.append(node("span", "", item.service), node("span", "", date(item.time)), node("span", "", `sessão ${short(item.session_id)}`));
    if (item.provenance === "sample") meta.append(node("span", "sample-tag", "AMOSTRA"));
    body.append(meta);
    row.append(score, body, evidenceButton(item.evidence));
    list.append(row);
  });
}

function renderSessions(sessions) {
  const list = $("agent-list"); clear(list);
  if (!sessions.length) { list.append(node("div", "empty", "Nenhuma sessão de agente identificável nesta janela. Emissores internos sem sinal de agente não são interpretados como agentes ativos.")); return; }
  sessions.forEach((item) => {
    const row = node("article", "agent-item");
    row.append(node("div", "agent-icon", item.service.slice(0, 1).toUpperCase()));
    const body = node("div", "agent-body");
    body.append(node("div", "agent-name", item.agent), node("div", "agent-meta", `${short(item.session_id)} · ${date(item.last_seen)} · ${item.signals.join(" + ")} · ${item.identity_basis}`));
    row.append(body, node("div", `activity ${item.activity === "recent" ? "recent" : ""}`, item.activity === "recent" ? "sinal recente" : "sem sinal recente"));
    list.append(row);
  });
}

function renderAnswer(answer) {
  const box = $("answer"); clear(box); box.hidden = false;
  box.append(node("div", "answer-kicker", answer.status === "unsupported" ? "FORA DO ESCOPO ATUAL" : `RESPOSTA · ${answer.status}`));
  box.append(node("div", "answer-text", answer.text));
  if (answer.coverage) box.append(node("div", "answer-note", `${answer.coverage.interpretation} Janela: ${date(answer.coverage.from)} a ${date(answer.coverage.to)}. Regra: ${answer.rule_version}.`));
  if (answer.recurrence_count) box.append(node("div", "answer-note", `Pergunta desta categoria feita ${answer.recurrence_count} vez(es). O texto original não é armazenado.`));
  if (answer.evidence?.length) {
    const row = node("div", "evidence-row");
    answer.evidence.forEach((evidence, index) => {
      const button = node("button", "evidence-chip", `Evidência ${index + 1} ↗`);
      button.type = "button";
      button.addEventListener("click", () => showEvidence(evidence));
      row.append(button);
    });
    box.append(row);
  }
  if (answer.supported_questions?.length) box.append(node("div", "answer-note", `Experimente: ${answer.supported_questions.join(" · ")}`));
}

function renderCuration(curation) {
  const indicators = $("indicators"); clear(indicators);
  if (!curation.indicators.length) indicators.append(node("div", "empty", "Nenhum indicador promovido. As perguntas frequentes aparecem abaixo para revisão."));
  curation.indicators.forEach((item) => {
    const card = node("article", "indicator-card");
    card.append(node("div", "indicator-label", item.title), node("div", "indicator-value", item.answer.metric));
    card.append(node("div", "agent-meta", `${item.answer.metric_label} · ${item.answer.status} · ${item.asks} perguntas`));
    const actions = node("div", "indicator-actions");
    if (item.answer.evidence.length) actions.append(evidenceButton(item.answer.evidence[0]));
    const remove = node("button", "small-button danger", "Remover"); remove.type = "button";
    remove.addEventListener("click", async () => {
      try { await api(`/api/indicators/${encodeURIComponent(item.intent)}`, { method: "DELETE" }); await refresh(); }
      catch (error) { notice(error.message); }
    });
    actions.append(remove); card.append(actions); indicators.append(card);
  });
  const candidates = $("candidates"); clear(candidates);
  curation.candidates.forEach((item) => {
    const row = node("div", "candidate");
    const label = node("div"); label.append(node("strong", "", item.title), node("span", "", `${item.asks} perguntas · mínimo ${curation.threshold}`));
    const promote = node("button", "small-button", "Promover indicador"); promote.type = "button";
    promote.addEventListener("click", async () => {
      try { await api("/api/indicators", { method: "POST", body: JSON.stringify({ intent: item.intent }) }); await refresh(); }
      catch (error) { notice(error.message); }
    });
    row.append(label, promote); candidates.append(row);
  });
}

async function refresh() {
  const button = $("refresh"); button.disabled = true;
  try {
    const dashboard = await api(`/api/dashboard${state.demo ? "?demo=1" : ""}`);
    state.dashboard = dashboard;
    renderCoverage(dashboard.coverage, dashboard.queue, dashboard.sessions, dashboard.queue_total, dashboard.sessions_total);
    renderQueue(dashboard.queue); renderSessions(dashboard.sessions);
    renderCuration(dashboard.curation);
  } catch (error) {
    $("connection").textContent = "Conexão indisponível"; $("connection").classList.remove("ok");
    notice(error.message);
  } finally { button.disabled = false; }
}

async function init() {
  try {
    const bootstrap = await api("/api/bootstrap"); state.csrf = bootstrap.csrf_token;
    bootstrap.questions.forEach((question) => {
      const button = node("button", "suggestion", question); button.type = "button";
      button.addEventListener("click", () => { $("question").value = question; $("question").focus(); });
      $("suggestions").append(button);
    });
    $("ask-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = $("question").value.trim(); if (!question) return;
      const button = $("ask-form").querySelector("button"); button.disabled = true;
      try {
        const answer = await api("/api/ask", { method: "POST", body: JSON.stringify({ question, demo: state.demo }) });
        renderAnswer(answer); await refresh();
      } catch (error) { notice(error.message); }
      finally { button.disabled = false; }
    });
    $("refresh").addEventListener("click", refresh);
    $("demo-mode").addEventListener("change", (event) => { state.demo = event.target.checked; $("answer").hidden = true; refresh(); });
    $("close-evidence").addEventListener("click", () => $("evidence-dialog").close());
    await refresh(); setInterval(refresh, 15000);
  } catch (error) { notice(error.message); }
}
init();
