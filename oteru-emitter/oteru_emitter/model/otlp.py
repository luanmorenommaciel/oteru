"""Conversão dict OTLP/JSON -> mensagem protobuf OTLP.

Usa as classes geradas de ``opentelemetry-proto`` como modelo neutro de
transporte: a MESMA mensagem serializa tanto para http/protobuf quanto para
gRPC. ``ParseDict`` reconstrói a mensagem fielmente a partir do JSON capturado.

Os imports de proto são lazy de propósito: o modo ``--dry-run`` do CLI valida
parsing/restamp sem exigir ``opentelemetry-proto``/``grpcio`` instalados.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # somente para type-checkers; não importa em runtime
    from google.protobuf.message import Message


def to_request(signal: str, payload: dict) -> Message:
    """Constrói o Export<Signal>ServiceRequest a partir do dict OTLP/JSON."""
    from google.protobuf.json_format import ParseDict

    if signal == "logs":
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )

        message: Message = ExportLogsServiceRequest()
    elif signal == "metrics":
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )

        message = ExportMetricsServiceRequest()
    elif signal == "traces":
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        message = ExportTraceServiceRequest()
    else:
        raise ValueError(f"sinal desconhecido: {signal!r}")

    # ignore_unknown_fields: tolera campos extras que o emissor possa ter
    # adicionado e que ainda não estejam no schema proto desta versão.
    ParseDict(payload, message, ignore_unknown_fields=True)
    return message
