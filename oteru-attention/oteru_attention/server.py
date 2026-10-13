"""Local HTTP console and bounded JSON API for the attention layer."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
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
        self._cache: dict[bool, tuple[float, Snapshot]] = {}
        self._lock = threading.Lock()

    def snapshot(self, *, demo: bool = False) -> Snapshot:
        with self._lock:
            cached = self._cache.get(demo)
            if cached and time.monotonic() - cached[0] < 3:
                return cached[1]
        rows, truncated = self.store.observations(
            hours=self.window_hours, limit=self.row_limit, demo=demo
        )
        snapshot = Snapshot(
            rows,
            now_ms=int(time.time() * 1000),
            hours=self.window_hours,
            demo=demo,
            truncated=truncated,
        )
        with self._lock:
            self._cache[demo] = (time.monotonic(), snapshot)
        return snapshot

    def dashboard(self, *, demo: bool = False) -> dict:
        snapshot = self.snapshot(demo=demo)
        curation = self.ledger.overview()
        queue = snapshot.queue()
        sessions = snapshot.sessions()
        indicators = [
            {**item, "answer": snapshot.answer(item["intent"])} for item in curation["promoted"]
        ]
        return {
            "as_of": snapshot.coverage()["to"],
            "coverage": snapshot.coverage(),
            "queue": queue[:50],
            "queue_total": len(queue),
            "sessions": sessions[:100],
            "sessions_total": len(sessions),
            "curation": {
                "threshold": curation["threshold"],
                "candidates": curation["candidates"],
                "indicators": indicators,
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
        # No raw question, prompt, identity or unsupported text is persisted.
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
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: dict) -> None:
            self._send(
                status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json"
            )

        def _demo(self) -> bool:
            return parse_qs(urlsplit(self.path).query).get("demo") == ["1"]

        def _read_json(self) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise ValueError("envie application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ValueError("corpo JSON fora do limite de 4096 bytes")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("o corpo deve ser um objeto JSON")
            return value

        def _check_write(self) -> bool:
            if not secrets.compare_digest(self.headers.get("X-Oteru-CSRF", ""), app.csrf_token):
                self._json(403, {"error": "token de escrita inválido"})
                return False
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self._json(403, {"error": "origem não permitida"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
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
                            "window_hours": app.window_hours,
                        },
                    )
                elif path == "/api/dashboard":
                    self._json(200, app.dashboard(demo=self._demo()))
                elif path.startswith("/api/evidence/"):
                    evidence_id = path.removeprefix("/api/evidence/")
                    if len(evidence_id) != 24 or any(
                        c not in "0123456789abcdef" for c in evidence_id
                    ):
                        self._json(400, {"error": "identificador de evidência inválido"})
                        return
                    evidence = app.snapshot(demo=self._demo()).evidence(evidence_id)
                    self._json(200, evidence) if evidence else self._json(
                        404, {"error": "evidência não encontrada na janela consultada"}
                    )
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

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            if not self._check_write():
                return
            try:
                body = self._read_json()
                path = urlsplit(self.path).path
                if path == "/api/ask":
                    if not isinstance(body.get("question"), str):
                        raise ValueError("question deve ser texto")
                    self._json(200, app.ask(body["question"], demo=body.get("demo") is True))
                elif path == "/api/indicators":
                    intent = body.get("intent")
                    if not isinstance(intent, str) or intent not in INTENTS:
                        raise ValueError("indicador desconhecido")
                    if app.ledger.promote(intent):
                        self._json(200, {"promoted": intent})
                    else:
                        self._json(
                            409, {"error": "a pergunta ainda não atingiu a recorrência mínima"}
                        )
                else:
                    self._json(404, {"error": "rota não encontrada"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except StoreUnavailable as exc:
                self._json(503, {"error": str(exc)})

        def do_DELETE(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            if not self._check_write():
                return
            path = urlsplit(self.path).path
            if not path.startswith("/api/indicators/"):
                self._json(404, {"error": "rota não encontrada"})
                return
            intent = path.removeprefix("/api/indicators/")
            try:
                removed = app.ledger.remove(intent)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"removed": removed})

    return Handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Oteru attention console")
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
    app = AttentionApp(store, ledger)
    with ThreadingHTTPServer((args.host, args.port), make_handler(app)) as server:
        print(f"Oteru Attention: http://{args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
