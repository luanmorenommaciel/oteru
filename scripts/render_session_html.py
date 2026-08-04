#!/usr/bin/env python3
"""Render one Claude Code session as a self-contained HTML timeline.

Reads the spans and log records a session left in ClickHouse and draws the
lineage: which model was called, in what order, which tools ran, how long the
agent sat waiting on a human, and what came back.

Standard library only, same discipline as scripts/check_pii.py — it has to run
before anyone installs anything.

The generated file outlives the data: with a 72h TTL the session disappears
from ClickHouse in three days, while the HTML keeps working.

usage:
    python3 scripts/render_session_html.py <session.id> [-o out.html]
    python3 scripts/render_session_html.py --list
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict

CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123/?user=otel&password=otel")

# Span roles, in the order they are drawn. Containers are deliberately muted:
# an `interaction` spans the whole turn and a `tool` spans its own children, so
# colouring them would paint the same milliseconds twice.
ROLE_CONTAINER = "container"
ROLE_MODEL = "model"
ROLE_WAIT = "wait"
ROLE_WORK = "work"

SPAN_ROLES = {
    "claude_code.interaction": ROLE_CONTAINER,
    "claude_code.tool": ROLE_CONTAINER,
    "claude_code.llm_request": ROLE_MODEL,
    "claude_code.tool.blocked_on_user": ROLE_WAIT,
    "claude_code.tool.execution": ROLE_WORK,
}

ROLE_LABEL = {
    ROLE_MODEL: "chamada de modelo",
    ROLE_WORK: "execução de tool",
    ROLE_WAIT: "esperando decisão humana",
    ROLE_CONTAINER: "contêiner (turno / tool)",
}


class ClickHouseError(RuntimeError):
    pass


def query(sql: str) -> list[dict]:
    """Runs SQL and returns rows; JSONEachRow keeps parsing to the stdlib."""
    body = (sql + "\nFORMAT JSONEachRow").encode("utf-8")
    try:
        with urllib.request.urlopen(CLICKHOUSE_URL, data=body, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise ClickHouseError(
            f"cannot reach ClickHouse at {CLICKHOUSE_URL}: {exc}\nis 'make up-clickhouse' running?"
        ) from exc
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


# --- data ------------------------------------------------------------------


def fetch_spans(session: str) -> list[dict]:
    rows = query(f"""
        SELECT TraceId, SpanId, ParentSpanId, SpanName,
               toUnixTimestamp64Nano(Timestamp) AS start_ns,
               Duration                         AS duration_ns,
               StatusCode,
               SpanAttributes,
               ResourceAttributes
        FROM otel.otel_traces
        WHERE SpanAttributes['session.id'] = '{session}'
        ORDER BY Timestamp
    """)
    for r in rows:
        r["start_ns"] = int(r["start_ns"])
        r["duration_ns"] = int(r["duration_ns"])
    return rows


def fetch_logs(session: str) -> list[dict]:
    rows = query(f"""
        SELECT toUnixTimestamp64Nano(Timestamp) AS ts_ns,
               Body, TraceId, SpanId, LogAttributes
        FROM otel.otel_logs
        WHERE LogAttributes['session.id'] = '{session}'
        ORDER BY Timestamp
    """)
    for r in rows:
        r["ts_ns"] = int(r["ts_ns"])
    return rows


def build_turns(spans: list[dict]) -> list[dict]:
    """Groups spans into turns and nests them by parent.

    A turn is one trace: Claude Code opens a fresh trace per user interaction,
    so TraceId is the turn key and needs no attribute to reconstruct.
    """
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for span in spans:
        by_trace[span["TraceId"]].append(span)

    turns = []
    for trace_id, group in by_trace.items():
        ids = {s["SpanId"] for s in group}
        # A root is a span whose parent is absent from the capture — either it
        # has none, or the parent was lost. Both must render, or a dropped
        # parent would silently hide its whole subtree.
        roots = [s for s in group if not s["ParentSpanId"] or s["ParentSpanId"] not in ids]
        if not roots:
            continue
        start = min(s["start_ns"] for s in group)
        end = max(s["start_ns"] + s["duration_ns"] for s in group)
        turns.append(
            {
                "trace_id": trace_id,
                "spans": group,
                "roots": sorted(roots, key=lambda s: s["start_ns"]),
                "start_ns": start,
                "end_ns": end,
                "duration_ns": end - start,
            }
        )
    return sorted(turns, key=lambda t: t["start_ns"])


def order_nested(turn: dict) -> list[tuple[dict, int]]:
    """Depth-first walk, so a child is drawn right under its parent."""
    children: dict[str, list[dict]] = defaultdict(list)
    for span in turn["spans"]:
        children[span["ParentSpanId"]].append(span)
    for kids in children.values():
        kids.sort(key=lambda s: s["start_ns"])

    ordered: list[tuple[dict, int]] = []

    def walk(span: dict, depth: int) -> None:
        ordered.append((span, depth))
        for child in children.get(span["SpanId"], []):
            walk(child, depth + 1)

    for root in turn["roots"]:
        walk(root, 0)
    return ordered


def span_label(span: dict) -> str:
    """What the bar says. The model or tool name is the point — the span name
    alone ('llm_request') does not answer 'which model did it call?'."""
    attrs = span.get("SpanAttributes", {})
    name = span["SpanName"].removeprefix("claude_code.")
    for key in ("model", "gen_ai.request.model", "tool_name"):
        if attrs.get(key):
            return f"{name} · {attrs[key]}"
    return name


def span_detail(span: dict) -> str:
    """The 'what came back' half of the lineage."""
    a = span.get("SpanAttributes", {})
    bits = []
    if a.get("input_tokens") or a.get("output_tokens"):
        bits.append(f"in {a.get('input_tokens', '0')} / out {a.get('output_tokens', '0')} tok")
    if a.get("cache_read_tokens") and a["cache_read_tokens"] != "0":
        bits.append(f"cache {a['cache_read_tokens']}")
    if a.get("ttft_ms"):
        bits.append(f"ttft {a['ttft_ms']}ms")
    for key in ("gen_ai.response.finish_reasons", "stop_reason", "decision", "source"):
        if a.get(key):
            bits.append(f"{key.rsplit('.', 1)[-1]}={a[key]}")
    if a.get("success") not in (None, "", "true"):
        bits.append(f"success={a['success']}")
    return " · ".join(bits)


def ms(ns: int) -> str:
    if ns >= 1_000_000_000:
        return f"{ns / 1e9:.2f} s"
    return f"{ns / 1e6:.0f} ms"


# --- rendering -------------------------------------------------------------

CSS = """
:root {
  color-scheme: light;
  --surface-1:#fcfcfb; --page:#f9f9f7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#898781;
  --gridline:#e1e0d9; --border:rgba(11,11,11,0.10);
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a;
  --warning:#fab219; --critical:#d03b3b; --mark-muted:#c3c2b7;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-1:#1a1a19; --page:#0d0d0d;
    --text-primary:#fff; --text-secondary:#c3c2b7; --text-muted:#898781;
    --gridline:#2c2c2a; --border:rgba(255,255,255,0.10);
    --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70;
    --mark-muted:#52514e;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1:#1a1a19; --page:#0d0d0d;
  --text-primary:#fff; --text-secondary:#c3c2b7; --text-muted:#898781;
  --gridline:#2c2c2a; --border:rgba(255,255,255,0.10);
  --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70;
  --mark-muted:#52514e;
}
* { box-sizing: border-box; }
body {
  margin:0; background:var(--page); color:var(--text-primary);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 48px 28px 72px; }
.eyebrow { font-size:12px; letter-spacing:.10em; text-transform:uppercase;
  color:var(--text-muted); margin-bottom:10px; }
h1 { font-size:31px; line-height:1.2; letter-spacing:-.02em; margin:0 0 10px; }
h2 { font-size:12px; letter-spacing:.10em; text-transform:uppercase;
  color:var(--text-muted); margin:52px 0 14px; padding-bottom:9px;
  border-bottom:1px solid var(--gridline); font-weight:600; }
.lede { color:var(--text-secondary); max-width:70ch; margin:0 0 6px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.88em;
  background:var(--surface-1); border:1px solid var(--border);
  border-radius:4px; padding:1px 5px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px; margin-bottom:8px; }
.tile { background:var(--surface-1); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.tile .label { font-size:12.5px; color:var(--text-muted); }
.tile .value { font-size:26px; font-weight:600; letter-spacing:-.02em;
  font-variant-numeric:tabular-nums; margin:2px 0; }
.tile .sub { font-size:12px; color:var(--text-muted); }
.turn { background:var(--surface-1); border:1px solid var(--border);
  border-radius:12px; padding:18px 20px; margin-bottom:16px; }
.turn h3 { margin:0 0 2px; font-size:16px; }
.turn .meta { font-size:12.5px; color:var(--text-muted); margin-bottom:14px;
  font-variant-numeric:tabular-nums; }
.row { display:grid; grid-template-columns:270px 1fr; align-items:center;
  gap:12px; margin-bottom:6px; }
.name { font-size:12.5px; color:var(--text-secondary); text-align:right;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.name.d1 { padding-left:10px; } .name.d2 { padding-left:20px; }
.name .dim { color:var(--text-muted); }
.track { position:relative; height:22px;
  background:linear-gradient(to right, var(--gridline) 1px, transparent 1px) repeat-x;
  background-size:10% 100%; }
.bar { position:absolute; top:3px; height:16px; border-radius:4px; min-width:2px;
  box-shadow:0 0 0 2px var(--surface-1); }
.bar.model { background:var(--series-1); }
.bar.work { background:var(--series-3); }
.bar.wait { background:var(--series-2); }
.bar.container { background:var(--mark-muted); }
.dur { position:absolute; top:2px; font-size:11.5px; color:var(--text-secondary);
  white-space:nowrap; font-variant-numeric:tabular-nums; }
.dur.inside { text-align:right; padding-right:7px; color:#fff;
  text-shadow:0 0 3px rgba(0,0,0,.30); overflow:hidden; }
.dur.inside.on-muted { color:var(--text-primary); text-shadow:none; }
.detail { grid-column:2; font-size:11.5px; color:var(--text-muted);
  margin:-3px 0 7px; font-variant-numeric:tabular-nums; }
.axis { display:grid; grid-template-columns:270px 1fr; gap:12px; margin-top:6px; }
.ticks { display:flex; justify-content:space-between; font-size:11.5px;
  color:var(--text-muted); font-variant-numeric:tabular-nums; }
.legend { display:flex; flex-wrap:wrap; gap:16px; margin:0 0 18px;
  font-size:12.5px; color:var(--text-secondary); }
.legend span { display:inline-flex; align-items:center; gap:7px; }
.swatch { width:11px; height:11px; border-radius:3px; display:inline-block; }
.events { margin-top:14px; border-top:1px solid var(--gridline); padding-top:12px; }
.events table { border-collapse:collapse; width:100%; font-size:12.5px; }
.events td { padding:3px 8px 3px 0; color:var(--text-secondary);
  vertical-align:top; }
.events td.seq { color:var(--text-muted); font-variant-numeric:tabular-nums;
  width:44px; }
.events td.body { white-space:nowrap; }
.note { background:var(--surface-1); border:1px solid var(--border);
  border-left:3px solid var(--series-1); border-radius:8px;
  padding:13px 16px; font-size:13.5px; color:var(--text-secondary);
  margin:16px 0; }
.note.warn { border-left-color:var(--warning); }
.scroll { overflow-x:auto; }
footer { margin-top:56px; padding-top:18px; border-top:1px solid var(--gridline);
  font-size:12.5px; color:var(--text-muted); }
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def render_turn(turn: dict, index: int, logs_by_trace: dict) -> str:
    total = max(turn["duration_ns"], 1)
    out = [f'<div class="turn"><h3>Turno {index}</h3>']

    root = turn["roots"][0]
    root_attrs = root.get("SpanAttributes", {})
    meta = [f"trace <code>{esc(turn['trace_id'][:16])}…</code>", ms(turn["duration_ns"])]
    if root_attrs.get("user_prompt_length"):
        meta.append(f"pergunta: {esc(root_attrs['user_prompt_length'])} caracteres")
    prompt_text = root_attrs.get("user_prompt")
    if prompt_text and prompt_text != "<REDACTED>":
        meta.append(f"“{esc(prompt_text[:120])}”")
    out.append(f'<div class="meta">{" · ".join(meta)}</div>')

    out.append('<div class="scroll">')
    for span, depth in order_nested(turn):
        role = SPAN_ROLES.get(span["SpanName"], ROLE_WORK)
        left = (span["start_ns"] - turn["start_ns"]) / total * 100
        width = max(span["duration_ns"] / total * 100, 0.4)
        # Duration label placement. A bar that fills the track has no room on
        # either side, so the label goes inside it; anchoring to the bar's left
        # edge would push it off the plot and over the span name.
        if width >= 18:
            label_class = "dur inside" + (" on-muted" if role == ROLE_CONTAINER else "")
            label_style = f"left:{left:.2f}%;width:{width:.2f}%"
        elif left + width <= 82:
            label_class = "dur"
            label_style = f"left:{left + width:.2f}%;padding-left:7px"
        else:
            label_class = "dur"
            label_style = f"right:{100 - left:.2f}%;padding-right:7px"

        out.append(
            f'<div class="row">'
            f'<div class="name d{min(depth, 2)}">{esc(span_label(span))}</div>'
            f'<div class="track">'
            f'<div class="bar {role}" style="left:{left:.2f}%;width:{width:.2f}%"></div>'
            f'<div class="{label_class}" style="{label_style}">{ms(span["duration_ns"])}</div>'
            f"</div></div>"
        )
        detail = span_detail(span)
        if detail:
            out.append(f'<div class="detail">{esc(detail)}</div>')

    out.append(
        f'<div class="axis"><div></div><div class="ticks">'
        f"<span>0</span><span>{ms(turn['duration_ns'] // 2)}</span>"
        f"<span>{ms(turn['duration_ns'])}</span></div></div>"
    )
    out.append("</div>")

    events = logs_by_trace.get(turn["trace_id"], [])
    if events:
        rows = []
        for log in events:
            attrs = log.get("LogAttributes", {})
            extra = [
                f"{k}={attrs[k]}"
                for k in ("tool_name", "decision", "source", "model", "success")
                if attrs.get(k)
            ]
            rows.append(
                f'<tr><td class="seq">{esc(attrs.get("event.sequence", "—"))}</td>'
                f'<td class="body"><code>{esc(log["Body"])}</code></td>'
                f"<td>{esc(' · '.join(extra))}</td></tr>"
            )
        out.append('<div class="events"><table>' + "".join(rows) + "</table></div>")

    out.append("</div>")
    return "".join(out)


def render(session: str, spans: list[dict], logs: list[dict]) -> str:
    turns = build_turns(spans)
    if not turns:
        raise SystemExit(f"no spans found for session {session}")

    logs_by_trace: dict[str, list[dict]] = defaultdict(list)
    orphan_logs = 0
    for log in logs:
        trace = log.get("TraceId") or ""
        if trace:
            logs_by_trace[trace].append(log)
        else:
            orphan_logs += 1

    resource = spans[0].get("ResourceAttributes", {})
    version = resource.get("service.version", "?")
    marker = resource.get("oteru.capture", "")

    total_in = total_out = 0
    models: dict[str, int] = defaultdict(int)
    tools: dict[str, int] = defaultdict(int)
    waited_ns = 0
    for span in spans:
        a = span.get("SpanAttributes", {})
        total_in += int(a.get("input_tokens") or 0)
        total_out += int(a.get("output_tokens") or 0)
        if a.get("model"):
            models[a["model"]] += 1
        if span["SpanName"] == "claude_code.tool" and a.get("tool_name"):
            tools[a["tool_name"]] += 1
        if span["SpanName"] == "claude_code.tool.blocked_on_user":
            waited_ns += span["duration_ns"]

    wall_ns = max(s["start_ns"] + s["duration_ns"] for s in spans) - min(
        s["start_ns"] for s in spans
    )

    tiles = [
        ("Turnos", str(len(turns)), f"{len(spans)} spans"),
        ("Duração", ms(wall_ns), "da sessão inteira"),
        (
            "Modelos",
            str(len(models)) if models else "—",
            ", ".join(sorted(models)) or "nenhum registrado",
        ),
        (
            "Tools",
            str(sum(tools.values())) if tools else "—",
            ", ".join(sorted(tools)) or "nenhuma",
        ),
        (
            "Tokens",
            f"{total_in + total_out:,}".replace(",", "."),
            f"{total_in:,} entrada / {total_out:,} saída".replace(",", "."),
        ),
        ("Esperando humano", ms(waited_ns) if waited_ns else "0 ms", "tempo parado em decisão"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="label">{esc(la)}</div>'
        f'<div class="value">{esc(v)}</div><div class="sub">{esc(s)}</div></div>'
        for la, v, s in tiles
    )

    legend = "".join(
        f'<span><i class="swatch" style="background:var(--{c})"></i>{esc(ROLE_LABEL[r])}</span>'
        for r, c in (
            (ROLE_MODEL, "series-1"),
            (ROLE_WORK, "series-3"),
            (ROLE_WAIT, "series-2"),
            (ROLE_CONTAINER, "mark-muted"),
        )
    )

    correlated = len(logs) - orphan_logs
    if not logs:
        # No logs at all is a different statement from "0% correlated" — saying
        # 0% would read as a defect when nothing was even measured.
        integrity_class = ""
        integrity = (
            "Esta sessão não tem log records no banco, só spans — então não há "
            "correlação log↔trace a medir aqui."
        )
    else:
        pct = round(100 * correlated / len(logs))
        integrity_class = " warn" if orphan_logs else ""
        integrity = (
            f"{correlated} de {len(logs)} log records carregam <code>TraceId</code> "
            f"(<strong>{pct}%</strong>)."
        )
        if orphan_logs:
            integrity += (
                f" Os {orphan_logs} órfãos não aparecem em turno nenhum. Na 2.1.191 "
                "isso atinge cerca de 25% dos registros; foi corrigido na 2.1.212."
            )

    turns_html = "".join(render_turn(t, i, logs_by_trace) for i, t in enumerate(turns, start=1))

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>oteru — timeline da sessão {esc(session[:8])}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">oteru · lineage de sessão</div>
  <h1>O que aconteceu, na ordem em que aconteceu</h1>
  <p class="lede">
    Sessão <code>{esc(session)}</code> do Claude Code {esc(version)}
    {f", captura <code>{esc(marker)}</code>" if marker else ""}.
    Cada turno abaixo é uma pergunta sua; as barras são os spans que o
    Claude Code emitiu enquanto respondia, posicionadas pelo tempo real.
  </p>

  <div class="note">
    <strong>Nada aqui exigiu ligar captura de conteúdo.</strong> Qual modelo foi
    chamado, em que ordem, quanto demorou e o que voltou já vem por padrão nos
    spans. As flags de conteúdo só acrescentariam o <em>texto</em> da pergunta e
    da resposta.
  </div>

  <h2>Panorama</h2>
  <div class="tiles">{tiles_html}</div>

  <h2>Timeline, turno a turno</h2>
  <div class="legend">{legend}</div>
  {turns_html}

  <h2>Integridade</h2>
  <div class="note{integrity_class}">
    {integrity}
    Cada turno é um <code>TraceId</code> distinto — o Claude Code abre um trace
    por interação, então a árvore reconstrói sem precisar de atributo nenhum.
  </div>

  <footer>
    Gerado por <code>scripts/render_session_html.py</code> a partir do ClickHouse.
    Os dados de origem expiram em 72h; esta página não.
    <strong>Uma sessão não é amostra</strong> — serve para entender o mecanismo,
    não para concluir sobre custo ou padrão de uso.
  </footer>
</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", nargs="?", help="session.id to render")
    parser.add_argument("-o", "--output", help="output path (default: local/sessao-<id>.html)")
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    args = parser.parse_args()

    try:
        if args.list or not args.session:
            rows = query("""
                SELECT SpanAttributes['session.id'] AS session,
                       any(ResourceAttributes['service.version']) AS version,
                       uniqExact(TraceId) AS turns,
                       count() AS spans
                FROM otel.otel_traces
                WHERE SpanAttributes['session.id'] != ''
                GROUP BY session ORDER BY max(Timestamp) DESC LIMIT 20
            """)
            if not rows:
                print("No sessions with spans. Traces are opt-in: see capture_session.sh env")
                return 1
            for r in rows:
                turns, spans_n = r["turns"], r["spans"]
                print(f"{r['session']}  {r['version']:>8}  {turns:>3} turnos  {spans_n:>4} spans")
            return 0 if args.list else 1

        spans = fetch_spans(args.session)
        if not spans:
            print(f"no spans for session {args.session}", file=sys.stderr)
            return 1
        logs = fetch_logs(args.session)
        page = render(args.session, spans, logs)
    except ClickHouseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = args.output or f"local/sessao-{args.session[:8]}.html"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(f"wrote {out}  ({len(spans)} spans, {len(logs)} log records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
