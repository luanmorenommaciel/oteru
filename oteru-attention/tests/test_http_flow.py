from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oteru_attention.curation import QuestionLedger
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
                    "event": "tool_result",
                    "tool_use_id": "call-1",
                    "success": "false",
                }
            ],
            False,
        )

    def query(self, sql: str):
        return [{"1": 1}]


def test_http_question_evidence_and_indicator_lifecycle(tmp_path) -> None:
    app = AttentionApp(FakeStore(), QuestionLedger(tmp_path / "curation.sqlite3"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def read(path: str) -> dict:
        with urlopen(base + path) as response:
            return json.load(response)

    def write(path: str, value: dict, *, token: str = "") -> dict:
        request = Request(
            base + path,
            data=json.dumps(value).encode(),
            headers={"Content-Type": "application/json", "X-Oteru-CSRF": token},
            method="POST",
        )
        with urlopen(request) as response:
            return json.load(response)

    try:
        token = read("/api/bootstrap")["csrf_token"]
        assert read("/healthz")["status"] == "ok"
        dashboard = read("/api/dashboard")
        evidence_id = dashboard["queue"][0]["evidence"]["id"]
        assert read(f"/api/evidence/{evidence_id}")["source_table"] == "otel.otel_logs"
        with pytest_raises_http(403):
            write("/api/ask", {"question": "Quais ferramentas falharam?"})
        for count in range(1, 4):
            answer = write("/api/ask", {"question": "Quais ferramentas falharam?"}, token=token)
            assert answer["recurrence_count"] == count
            assert answer["evidence"][0]["id"] == evidence_id
        assert read("/api/dashboard")["curation"]["candidates"][0]["intent"] == "failures"
        assert write("/api/indicators", {"intent": "failures"}, token=token) == {
            "promoted": "failures"
        }
        assert read("/api/dashboard")["curation"]["indicators"][0]["answer"]["metric"] == 1
        unsupported = write("/api/ask", {"question": "Apague o banco"}, token=token)
        assert unsupported["status"] == "unsupported" and unsupported["evidence"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class pytest_raises_http:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        assert exception_type is HTTPError
        assert exception.code == self.status
        return True
