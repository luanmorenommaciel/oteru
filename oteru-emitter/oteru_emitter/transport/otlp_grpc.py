"""OTLP transport over gRPC (port 4317).

Reuses the SAME proto message as the HTTP transport — only the channel
changes. Requires the collector to have a gRPC receiver enabled
(`receivers.otlp.protocols.grpc`) — present in the sibling directory's
config, `oteru-collector/oteru-collector-config.yml`.
"""

from __future__ import annotations

import grpc


class GrpcTransport:
    def __init__(
        self,
        endpoint: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        # gRPC metadata keys must be lowercase
        self.metadata = [(name.lower(), value) for name, value in (headers or {}).items()]
        self.channel = grpc.insecure_channel(endpoint)
        self._stubs: dict[str, object] = {}

    def _stub(self, signal: str):
        if signal in self._stubs:
            return self._stubs[signal]
        if signal == "logs":
            from opentelemetry.proto.collector.logs.v1 import logs_service_pb2_grpc

            stub = logs_service_pb2_grpc.LogsServiceStub(self.channel)
        elif signal == "metrics":
            from opentelemetry.proto.collector.metrics.v1 import (
                metrics_service_pb2_grpc,
            )

            stub = metrics_service_pb2_grpc.MetricsServiceStub(self.channel)
        elif signal == "traces":
            from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

            stub = trace_service_pb2_grpc.TraceServiceStub(self.channel)
        else:
            raise ValueError(f"unknown signal: {signal!r}")
        self._stubs[signal] = stub
        return stub

    def send(self, signal: str, request: object) -> object:
        stub = self._stub(signal)
        return stub.Export(  # type: ignore[attr-defined]
            request, timeout=self.timeout, metadata=self.metadata or None
        )

    def close(self) -> None:
        self.channel.close()
