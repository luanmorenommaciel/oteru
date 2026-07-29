"""OTLP/JSON dict -> OTLP protobuf message conversion.

Uses the generated ``opentelemetry-proto`` classes as a transport-neutral
model: the SAME message serializes for both http/protobuf and gRPC.
``ParseDict`` faithfully rebuilds the message from the captured JSON.

The proto imports are lazy on purpose: the CLI's ``--dry-run`` mode validates
parsing/restamp without requiring ``opentelemetry-proto``/``grpcio``.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-checkers only; not imported at runtime
    from google.protobuf.message import Message

# OTLP/JSON encodes trace/span IDs as lowercase hex (that is what the
# collector's `file` exporter writes), but protobuf's canonical JSON mapping
# expects base64 for `bytes` fields — ParseDict would decode the hex string as
# base64 and reject it ("invalid TraceID length"). These keys carry IDs on
# spans, span links and log records; the expected value is their byte length.
ID_KEY_BYTE_LENGTHS = {
    "traceId": 16,
    "spanId": 8,
    "parentSpanId": 8,
}


def _hex_to_base64(value: str, byte_length: int) -> str | None:
    """Re-encodes a hex ID as base64, or returns None if it is not that hex."""
    if len(value) != byte_length * 2:
        return None
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return None
    return base64.b64encode(raw).decode("ascii")


def normalize_ids(node: object) -> object:
    """Returns a copy of the payload with hex trace/span IDs re-encoded.

    Values that are not hex of the expected length are left untouched — an
    empty string (Claude Code's logs carry empty trace IDs) or an already
    base64-encoded ID both pass through unchanged.
    """
    if isinstance(node, dict):
        out: dict = {}
        for key, value in node.items():
            byte_length = ID_KEY_BYTE_LENGTHS.get(key)
            if byte_length is not None and isinstance(value, str) and value:
                converted = _hex_to_base64(value, byte_length)
                out[key] = value if converted is None else converted
            else:
                out[key] = normalize_ids(value)
        return out
    if isinstance(node, list):
        return [normalize_ids(item) for item in node]
    return node


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
    ParseDict(normalize_ids(payload), message, ignore_unknown_fields=True)
    return message
