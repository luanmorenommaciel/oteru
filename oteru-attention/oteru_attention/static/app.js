const $ = (id) => document.getElementById(id);
let demo = false;

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
refresh();
setInterval(refresh, 15000);
