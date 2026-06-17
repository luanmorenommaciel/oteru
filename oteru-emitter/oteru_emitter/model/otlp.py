"""OTLP/JSON dict -> OTLP protobuf message conversion.

Uses the generated ``opentelemetry-proto`` classes as a transport-neutral
model: the SAME message serializes for both http/protobuf and gRPC.
``ParseDict`` faithfully rebuilds the message from the captured JSON.

The proto imports are lazy on purpose: the CLI's ``--dry-run`` mode validates
parsing/restamp without requiring ``opentelemetry-proto``/``grpcio``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-checkers only; not imported at runtime
    from google.protobuf.message import Message


def to_request(signal: str, payload: dict) -> Message:
    """Builds the Export<Signal>ServiceRequest from the OTLP/JSON dict."""
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
        raise ValueError(f"unknown signal: {signal!r}")

    # ignore_unknown_fields: tolerates extra fields the emitter may have
    # added that are not yet in this version's proto schema.
    ParseDict(payload, message, ignore_unknown_fields=True)
    return message
