const $ = (id) => document.getElementById(id);
let demo = false;
let csrf = "";

function date(value) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

async function refresh() {
  try {
    const response = await fetch(`/api/dashboard${demo ? "?demo=1" : ""}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    $("connection").textContent = "ClickHouse conectado";
    $("connection").classList.add("ok");
    $("coverage-title").textContent = data.coverage.demo ? "Amostra incluída" : "Dados observados";
    $("coverage-window").textContent = `Janela: ${date(data.coverage.from)} → ${date(data.coverage.to)}`;
    $("coverage-detail").textContent = `${data.coverage.logs} logs · ${data.coverage.agent_spans} spans de agente`;
    $("queue-count").textContent = data.queue_total > data.queue.length ? `${data.queue.length} de ${data.queue_total}` : String(data.queue_total);
    const list = $("queue-list");
    list.replaceChildren();
    for (const item of data.queue) {
      const row = document.createElement("article");
      row.className = "queue-item";
      const score = document.createElement("div");
      score.className = "severity";
      score.textContent = item.score;
      const body = document.createElement("div");
      const title = document.createElement("div");
      title.className = "item-title";
      title.textContent = item.title;
      const detail = document.createElement("div");
      detail.className = "item-reason";
      detail.textContent = `${item.reason} · ${item.service} · ${date(item.time)}`;
      body.append(title, detail);
      row.append(score, body);
      list.append(row);
    }
    if (!data.queue.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Nenhuma ocorrência prioritária observada nesta janela.";
      list.append(empty);
    }
    $("notice").hidden = !data.coverage.truncated;
    $("notice").textContent = data.coverage.truncated ? "A consulta atingiu o limite de registros; os totais podem estar incompletos." : "";
  } catch (error) {
    $("connection").textContent = "Conexão indisponível";
    $("notice").hidden = false;
    $("notice").textContent = error.message;
  }
}

$("refresh").addEventListener("click", refresh);
$("demo-mode").addEventListener("change", (event) => { demo = event.target.checked; refresh(); });
$("ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Oteru-CSRF": csrf },
      body: JSON.stringify({ question: $("question").value, demo }),
    });
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error || `HTTP ${response.status}`);
    const box = $("answer");
    box.replaceChildren();
    box.hidden = false;
    const text = document.createElement("p");
    text.className = "answer-text";
    text.textContent = answer.text;
    box.append(text);
    if (answer.coverage) {
      const coverage = document.createElement("p");
      coverage.className = "answer-note";
      coverage.textContent = `Janela: ${date(answer.coverage.from)} a ${date(answer.coverage.to)} · ${answer.status}`;
      box.append(coverage);
    }
  } catch (error) { $("notice").hidden = false; $("notice").textContent = error.message; }
});
fetch("/api/bootstrap").then((response) => response.json()).then((data) => {
  csrf = data.csrf_token;
  for (const question of data.questions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.textContent = question;
    button.addEventListener("click", () => { $("question").value = question; });
    $("suggestions").append(button);
  }
});
refresh();
setInterval(refresh, 15000);
