"""OTLP transport interface."""

from __future__ import annotations

from typing import Protocol

DEFAULT_HTTP_ENDPOINT = "http://localhost:4318"
DEFAULT_GRPC_ENDPOINT = "localhost:4317"


class Transport(Protocol):
    """Sends an Export<Signal>ServiceRequest message to the collector."""

    def send(self, signal: str, request: object) -> object: ...

    def close(self) -> None: ...
