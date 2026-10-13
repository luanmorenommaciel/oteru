"""Local HTTP console for the prioritized attention queue."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .clickhouse import ClickHouseStore, StoreConfig, StoreUnavailable
from .core import INTENTS, Snapshot, classify_question
from .curation import QuestionLedger

STATIC_DIR = Path(__file__).parent / "static"


class AttentionApp:
    def __init__(
        self,
        store: ClickHouseStore,
        ledger: QuestionLedger,
        *,
        window_hours: int = 24,
        row_limit: int = 5000,
    ):
        if not 1 <= window_hours <= 168 or not 1 <= row_limit <= 10000:
            raise ValueError("window or row limit outside supported bounds")
        self.store = store
        self.ledger = ledger
        self.window_hours = window_hours
        self.row_limit = row_limit
        self.csrf_token = secrets.token_urlsafe(32)

    def snapshot(self, *, demo: bool = False) -> Snapshot:
        rows, truncated = self.store.observations(
            hours=self.window_hours, limit=self.row_limit, demo=demo
        )
        return Snapshot(
            rows,
            now_ms=int(time.time() * 1000),
            hours=self.window_hours,
            demo=demo,
            truncated=truncated,
        )

    def dashboard(self, *, demo: bool = False) -> dict:
        snapshot = self.snapshot(demo=demo)
        queue = snapshot.queue()
        curation = self.ledger.overview()
        return {
            "coverage": snapshot.coverage(),
            "queue": queue[:50],
            "queue_total": len(queue),
            "curation": {
                "threshold": curation["threshold"],
                "candidates": curation["candidates"],
                "indicators": [
                    {**item, "answer": snapshot.answer(item["intent"])}
                    for item in curation["promoted"]
                ],
            },
        }

    def ask(self, question: str, *, demo: bool = False) -> dict:
        if not 1 <= len(question.strip()) <= 300:
            raise ValueError("a pergunta deve ter entre 1 e 300 caracteres")
        intent = classify_question(question)
        if intent is None:
            return {
                "intent": None,
                "status": "unsupported",
                "text": "Ainda não consigo responder essa pergunta com uma consulta validada.",
                "evidence": [],
                "supported_questions": [spec["example"] for spec in INTENTS.values()],
            }
        answer = self.snapshot(demo=demo).answer(intent)
        if answer["status"] in {"answered", "partial"}:
            answer["recurrence_count"] = self.ledger.record(intent)
        return answer


def make_handler(app: AttentionApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OteruAttention/0.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if path == "/healthz":
                    app.store.query("SELECT 1 FORMAT JSONEachRow")
                    self._json(200, {"status": "ok"})
                elif path == "/api/bootstrap":
                    self._json(
                        200,
                        {
                            "csrf_token": app.csrf_token,
                            "questions": [spec["example"] for spec in INTENTS.values()],
                        },
                    )
                elif path == "/api/dashboard":
                    demo = parse_qs(urlsplit(self.path).query).get("demo") == ["1"]
                    self._json(200, app.dashboard(demo=demo))
                elif path.startswith("/api/evidence/"):
                    evidence_id = path.removeprefix("/api/evidence/")
                    if len(evidence_id) != 24 or any(
                        char not in "0123456789abcdef" for char in evidence_id
                    ):
                        self._json(400, {"error": "identificador de evidência inválido"})
                        return
                    demo = parse_qs(urlsplit(self.path).query).get("demo") == ["1"]
                    evidence = app.snapshot(demo=demo).evidence(evidence_id)
                    if evidence:
                        self._json(200, evidence)
                    else:
                        self._json(404, {"error": "evidência não encontrada na janela consultada"})
                elif path in {"/", "/index.html", "/app.js", "/style.css"}:
                    name = "index.html" if path == "/" else path[1:]
                    media = {
                        "index.html": "text/html; charset=utf-8",
                        "app.js": "text/javascript; charset=utf-8",
                        "style.css": "text/css; charset=utf-8",
                    }[name]
                    self._send(200, (STATIC_DIR / name).read_bytes(), media)
                else:
                    self._json(404, {"error": "rota não encontrada"})
            except StoreUnavailable as exc:
                self._json(503, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            if not secrets.compare_digest(self.headers.get("X-Oteru-CSRF", ""), app.csrf_token):
                self._json(403, {"error": "token de escrita inválido"})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self._json(403, {"error": "origem não permitida"})
                return
            try:
                path = urlsplit(self.path).path
                if path not in {"/api/ask", "/api/indicators"}:
                    self._json(404, {"error": "rota não encontrada"})
                    return
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ValueError("envie application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 4096:
                    raise ValueError("corpo JSON fora do limite de 4096 bytes")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("o corpo deve ser um objeto JSON")
                if path == "/api/ask":
                    if not isinstance(body.get("question"), str):
                        raise ValueError("question deve ser texto")
                    self._json(200, app.ask(body["question"], demo=body.get("demo") is True))
                else:
                    intent = body.get("intent")
                    if not isinstance(intent, str) or intent not in INTENTS:
                        raise ValueError("indicador desconhecido")
                    if app.ledger.promote(intent):
                        self._json(200, {"promoted": intent})
                    else:
                        self._json(
                            409, {"error": "a pergunta ainda não atingiu a recorrência mínima"}
                        )
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except StoreUnavailable as exc:
                self._json(503, {"error": str(exc)})

        def do_DELETE(self) -> None:  # noqa: N802
            if not secrets.compare_digest(self.headers.get("X-Oteru-CSRF", ""), app.csrf_token):
                self._json(403, {"error": "token de escrita inválido"})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self._json(403, {"error": "origem não permitida"})
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/indicators/"):
                self._json(404, {"error": "rota não encontrada"})
                return
            try:
                removed = app.ledger.remove(path.removeprefix("/api/indicators/"))
                self._json(200, {"removed": removed})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Oteru attention queue")
    parser.add_argument("--host", default=os.getenv("OTERU_ATTENTION_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OTERU_ATTENTION_PORT", "8081")))
    args = parser.parse_args(argv)
    store = ClickHouseStore(
        StoreConfig(
            url=os.getenv("OTERU_CLICKHOUSE_URL", "http://127.0.0.1:8123"),
            user=os.getenv("OTERU_CLICKHOUSE_USER", "otel"),
            password=os.getenv("OTERU_CLICKHOUSE_PASSWORD", "otel"),
        )
    )
    ledger = QuestionLedger(os.getenv("OTERU_ATTENTION_DB", "state/attention.sqlite3"))
    with ThreadingHTTPServer(
        (args.host, args.port), make_handler(AttentionApp(store, ledger))
    ) as server:
        print(f"Oteru Attention: http://{args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
