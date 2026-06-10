"""Replay source: reads an OTLP/JSON capture (one batch per line) and turns it
into a sequence of faithful ``Batch`` objects, preserving structure and types.

The expected format is exactly what the collector's `file` exporter writes:
each line is an object with one of the keys ``resourceLogs`` /
``resourceMetrics`` / ``resourceSpans`` (the body of an
``Export<Signal>ServiceRequest`` in OTLP/JSON).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass

# OTLP/JSON key -> signal name
SIGNAL_BY_KEY = {
    "resourceLogs": "logs",
    "resourceMetrics": "metrics",
    "resourceSpans": "traces",
}

# Timestamps that represent "when the event happened" — used to anchor the
# replay pacing. startTimeUnixNano is left out on purpose: it usually points
# at the session start and would distort the deltas.
ANCHOR_TIME_KEYS = {"timeUnixNano", "observedTimeUnixNano"}


@dataclass
class Batch:
    """An OTLP batch loaded from the capture file."""

    signal: str  # "logs" | "metrics" | "traces"
    payload: dict  # raw OTLP/JSON dict (mutable — restamp operates here)
    anchor_ns: int | None  # smallest event timestamp in the batch (ns), for pacing


def iter_timestamps(node: object) -> Iterator[int]:
    """Recursively walks the dict/list and yields each event timestamp."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ANCHOR_TIME_KEYS:
                try:
                    yield int(value)
                except (TypeError, ValueError):
                    pass
            yield from iter_timestamps(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_timestamps(item)


def _detect_signal(obj: dict) -> str | None:
    for key, signal in SIGNAL_BY_KEY.items():
        if key in obj:
            return signal
    return None


def load_batches(path: str) -> list[Batch]:
    """Loads every batch from the file, in arrival (line) order."""
    batches: list[Batch] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno}: invalid JSON: {exc}") from exc
            signal = _detect_signal(obj)
            if signal is None:
                # line without a recognizable OTLP payload — silently ignored
                continue
            anchors = list(iter_timestamps(obj))
            anchor_ns = min(anchors) if anchors else None
            batches.append(Batch(signal=signal, payload=obj, anchor_ns=anchor_ns))
    return batches
