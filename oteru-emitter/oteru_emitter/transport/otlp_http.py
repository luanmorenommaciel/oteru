"""Transporte OTLP via HTTP com corpo protobuf (http/protobuf).

POST para /v1/logs, /v1/metrics, /v1/traces com Content-Type
application/x-protobuf — exatamente o que o Claude Code faz com
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf.
"""

from __future__ import annotations

import requests

PATHS = {
    "logs": "/v1/logs",
    "metrics": "/v1/metrics",
    "traces": "/v1/traces",
}


class HttpTransport:
    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self.base = endpoint.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/x-protobuf"

    def send(self, signal: str, request: object) -> int:
        url = self.base + PATHS[signal]
        data = request.SerializeToString()  # type: ignore[attr-defined]
        response = self.session.post(url, data=data, timeout=self.timeout)
        response.raise_for_status()
        return response.status_code

    def close(self) -> None:
        self.session.close()
