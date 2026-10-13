"""Local HTTP console for the prioritized attention queue."""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .clickhouse import ClickHouseStore, StoreConfig, StoreUnavailable
from .core import Snapshot

STATIC_DIR = Path(__file__).parent / "static"


class AttentionApp:
    def __init__(self, store: ClickHouseStore, *, window_hours: int = 24, row_limit: int = 5000):
        if not 1 <= window_hours <= 168 or not 1 <= row_limit <= 10000:
            raise ValueError("window or row limit outside supported bounds")
        self.store = store
        self.window_hours = window_hours
        self.row_limit = row_limit

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
        return {
            "coverage": snapshot.coverage(),
            "queue": queue[:50],
            "queue_total": len(queue),
        }


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
                elif path == "/api/dashboard":
                    demo = parse_qs(urlsplit(self.path).query).get("demo") == ["1"]
                    self._json(200, app.dashboard(demo=demo))
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
    with ThreadingHTTPServer((args.host, args.port), make_handler(AttentionApp(store))) as server:
        print(f"Oteru Attention: http://{args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
