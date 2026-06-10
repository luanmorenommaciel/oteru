"""Transporte OTLP via gRPC (porta 4317).

Reusa a MESMA mensagem proto do transporte HTTP — só muda o canal. Exige que o
collector tenha um receiver gRPC habilitado (no repo hello-world só o `http`
está configurado; adicionar `grpc:` em receivers.otlp.protocols).
"""

from __future__ import annotations

import grpc


class GrpcTransport:
    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
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
            raise ValueError(f"sinal desconhecido: {signal!r}")
        self._stubs[signal] = stub
        return stub

    def send(self, signal: str, request: object) -> object:
        stub = self._stub(signal)
        return stub.Export(request, timeout=self.timeout)  # type: ignore[attr-defined]

    def close(self) -> None:
        self.channel.close()
