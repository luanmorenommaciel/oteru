"""Manual source: forges an OTLP traces payload from a hand-written spec.

Data bootstrap (#43): before any agent is instrumented, someone has to be able
to say "this is what a run looks like" and get real OTLP out of it. The spec is
deliberately small — span names, a local ``id`` per span, ``parent`` links and
millisecond offsets — and everything OTLP-specific (trace/span IDs, nanosecond
timestamps, AnyValue typing, enum codes) is derived here.

The output is exactly what ``replay`` reads: one OTLP/JSON traces batch per
trace, IDs in lowercase hex like the collector's ``file`` exporter writes. So
forging composes with the existing pipeline instead of growing a second one:

    oteru-emitter forge spec.json -o run.json
    oteru-emitter replay run.json --profile generic

Spec shape::

    {
      "resource": {"service.name": "my-agent"},          # optional
      "scope": {"name": "oteru.manual", "version": "1"},  # optional
      "traces": [
        {"spans": [
          {"id": "root", "name": "invoke_agent", "duration_ms": 1200},
          {"id": "llm", "name": "chat", "parent": "root",
           "start_ms": 15, "duration_ms": 800, "kind": "client",
           "status": "ok", "attributes": {"gen_ai.request.model": "x"}}
        ]}
      ]
    }

Stdlib-only, so ``forge`` works without ``opentelemetry-proto`` installed.
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

DEFAULT_SERVICE_NAME = "oteru-manual"
DEFAULT_SCOPE = {"name": "oteru.manual"}
MS = 1_000_000
# OTLP field limits: AnyValue.int_value is int64, *_time_unix_nano are fixed64.
# A value past them would be written here and only fail later, in the protobuf
# conversion ("Value out of range") — so forge refuses it up front.
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
UINT64_MAX = 2**64 - 1

# OTLP SpanKind / Status.code enum values, by the names the spec accepts.
SPAN_KINDS = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}
STATUS_CODES = {"unset": 0, "ok": 1, "error": 2}


class SpecError(ValueError):
    """The spec cannot be turned into a coherent trace."""


def load_spec(path: str | Path) -> dict:
    """Reads a spec file. Raises OSError / json.JSONDecodeError as-is."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def any_value(value: object, key: str) -> dict:
    """Python value -> OTLP/JSON AnyValue. bool is checked before int on purpose:
    in Python ``True`` is an ``int``, and it must not become ``intValue: "1"``."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        if not INT64_MIN <= value <= INT64_MAX:
            raise SpecError(f"attribute {key!r}: {value} is outside int64")
        return {"intValue": str(value)}  # int64 is a string in OTLP/JSON
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpecError(f"attribute {key!r}: {value} is not a finite number")
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [any_value(v, key) for v in value]}}
    raise SpecError(
        f"attribute {key!r}: unsupported value {value!r} "
        "(use a string, number, boolean or a list of those)"
    )


def _attributes(mapping: dict, where: str) -> list[dict]:
    try:
        return [{"key": k, "value": any_value(v, k)} for k, v in mapping.items()]
    except SpecError as exc:
        raise SpecError(f"{where}: {exc}") from None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _new_id(rng: random.Random, byte_length: int) -> str:
    # An all-zero ID is invalid in OTLP; vanishingly unlikely, but cheap to rule out.
    while True:
        value = rng.getrandbits(byte_length * 8)
        if value:
            return f"{value:0{byte_length * 2}x}"


def _validate_trace(index: int, spans: list) -> dict[str, dict]:
    """Checks one trace's spans; returns them keyed by local id."""
    if not spans:
        raise SpecError(f"trace #{index}: no spans")
    by_id: dict[str, dict] = {}
    for position, span in enumerate(spans):
        where = f"trace #{index}, span #{position}"
        for field in ("id", "name"):
            if not isinstance(span.get(field), str) or not span[field]:
                raise SpecError(f"{where}: missing or empty '{field}'")
        where = f"trace #{index}, span {span['id']!r}"
        if span["id"] in by_id:
            raise SpecError(f"{where}: duplicate id")
        duration = span.get("duration_ms")
        if not _is_number(duration) or duration < 0:
            raise SpecError(f"{where}: 'duration_ms' must be a finite number >= 0")
        start = span.get("start_ms", 0)
        if not _is_number(start) or start < 0:
            raise SpecError(f"{where}: 'start_ms' must be a finite number >= 0")
        if span.get("kind", "internal") not in SPAN_KINDS:
            raise SpecError(
                f"{where}: unknown kind {span['kind']!r} (one of {', '.join(SPAN_KINDS)})"
            )
        if span.get("status", "unset") not in STATUS_CODES:
            raise SpecError(
                f"{where}: unknown status {span['status']!r} (one of {', '.join(STATUS_CODES)})"
            )
        by_id[span["id"]] = span

    for span in spans:
        parent = span.get("parent")
        if parent is not None and parent not in by_id:
            raise SpecError(f"trace #{index}, span {span['id']!r}: unknown parent {parent!r}")
    for span in spans:  # walk up from every span; revisiting a span means a cycle
        seen, current = set(), span
        while current.get("parent") is not None:
            if current["id"] in seen:
                raise SpecError(f"trace #{index}, span {span['id']!r}: parent cycle")
            seen.add(current["id"])
            current = by_id[current["parent"]]
    return by_id


def forge(spec: dict, *, seed: int | None = None, base_ns: int | None = None) -> list[dict]:
    """Spec -> list of OTLP/JSON traces batches (one per trace, spec order).

    ``base_ns`` is the time ``start_ms`` offsets count from (default: now —
    replay restamps to "now" anyway). ``seed`` makes the generated IDs
    reproducible.
    """
    traces = spec.get("traces")
    if not isinstance(traces, list) or not traces:
        raise SpecError("spec must hold at least one trace under 'traces'")

    rng = random.Random(seed)
    base = time.time_ns() if base_ns is None else base_ns
    resource = {"service.name": DEFAULT_SERVICE_NAME, **spec.get("resource", {})}
    scope = spec.get("scope", DEFAULT_SCOPE)

    batches = []
    for index, trace in enumerate(traces):
        spans = trace.get("spans") if isinstance(trace, dict) else None
        by_id = _validate_trace(index, spans or [])
        trace_id = _new_id(rng, 16)
        span_ids = {local: _new_id(rng, 8) for local in by_id}

        otlp_spans = []
        for span in spans:
            where = f"trace #{index}, span {span['id']!r}"
            start = base + round(span.get("start_ms", 0) * MS)
            end = start + round(span["duration_ms"] * MS)
            if end > UINT64_MAX:
                raise SpecError(
                    f"{where}: end time {end} ns is past uint64 (start_ms + duration_ms too large)"
                )
            status = {"code": STATUS_CODES[span.get("status", "unset")]}
            if "status_message" in span:
                status["message"] = str(span["status_message"])
            out = {
                "traceId": trace_id,
                "spanId": span_ids[span["id"]],
                "name": span["name"],
                "kind": SPAN_KINDS[span.get("kind", "internal")],
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "attributes": _attributes(span.get("attributes", {}), where),
                "status": status,
            }
            if span.get("parent") is not None:
                out["parentSpanId"] = span_ids[span["parent"]]
            otlp_spans.append(out)

        batches.append(
            {
                "resourceSpans": [
                    {
                        "resource": {"attributes": _attributes(resource, "resource")},
                        "scopeSpans": [{"scope": scope, "spans": otlp_spans}],
                    }
                ]
            }
        )
    return batches
