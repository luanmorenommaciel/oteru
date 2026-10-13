"""Bounded Ask Oteru HTTP contract."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oteru_attention.server import AttentionApp, make_handler


class FakeStore:
    def observations(self, *, hours: int, limit: int, demo: bool):
        return (
            [
                {
                    "ts_ms": 1_700_000_000_000,
                    "source": "log",
                    "service": "fixture-agent",
                    "session_id": "fixture-session",
                    "event": "codex.tool_result",
                    "tool_use_id": "call-1",
                    "success": "false",
                }
            ],
            False,
        )

    def query(self, sql: str):
        return [{"1": 1}]


def test_ask_uses_supported_intent_and_requires_csrf() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(AttentionApp(FakeStore())))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/bootstrap") as response:
            token = json.load(response)["csrf_token"]
        body = json.dumps({"question": "Quais ferramentas falharam?"}).encode()
        try:
            urlopen(
                Request(
                    base + "/api/ask",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            )
            raise AssertionError("missing CSRF token should fail")
        except HTTPError as exc:
            assert exc.code == 403
        request = Request(
            base + "/api/ask",
            data=body,
            headers={"Content-Type": "application/json", "X-Oteru-CSRF": token},
            method="POST",
        )
        with urlopen(request) as response:
            answer = json.load(response)
        assert answer["intent"] == "failures"
        assert answer["metric"] == 1
        assert answer["evidence"][0]["event"] == "codex.tool_result"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
