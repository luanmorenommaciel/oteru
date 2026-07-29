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

# CLI-facing signal names (singular, as in ``--emit log,metric,trace``) ->
# internal signal name. The three are independent: any combination is valid
# and none implies another (a trace does not require a log or a metric).
SIGNAL_BY_CLI_NAME = {
    "log": "logs",
    "metric": "metrics",
    "trace": "traces",
}

# Canonical order for reporting, so ``--emit trace,log`` always prints
# "log,metric,trace" order rather than echoing the user's ordering.
CLI_SIGNAL_NAMES = tuple(SIGNAL_BY_CLI_NAME)

# Timestamps that represent "when the event happened" — used to anchor the
# replay pacing. startTimeUnixNano is left out on purpose: on metrics it points
# at the cumulative-series start and would distort the deltas.
ANCHOR_TIME_KEYS = {"timeUnixNano", "observedTimeUnixNano"}

# Spans are the exception: they carry no timeUnixNano at all, only
# start/endTimeUnixNano. Without startTimeUnixNano a traces batch would have no
# anchor, so restamp would find nothing to shift and replayed spans would keep
# their original (stale) timestamps.
ANCHOR_TIME_KEYS_TRACES = ANCHOR_TIME_KEYS | {"startTimeUnixNano"}


def anchor_keys_for(signal: str) -> set[str]:
    """Timestamp keys usable as a pacing anchor for the given signal."""
    return ANCHOR_TIME_KEYS_TRACES if signal == "traces" else ANCHOR_TIME_KEYS


@dataclass
class Batch:
    """An OTLP batch loaded from the capture file."""

    signal: str  # "logs" | "metrics" | "traces"
    payload: dict  # raw OTLP/JSON dict (mutable — restamp operates here)
    anchor_ns: int | None  # smallest event timestamp in the batch (ns), for pacing


def iter_timestamps(node: object, keys: set[str] | None = None) -> Iterator[int]:
    """Recursively walks the dict/list and yields each event timestamp."""
    keys = ANCHOR_TIME_KEYS if keys is None else keys
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys:
                try:
                    yield int(value)
                except (TypeError, ValueError):
                    pass
            yield from iter_timestamps(value, keys)
    elif isinstance(node, list):
        for item in node:
            yield from iter_timestamps(item, keys)


def _detect_signal(obj: dict) -> str | None:
    for key, signal in SIGNAL_BY_KEY.items():
        if key in obj:
            return signal
    return None


def select_signals(batches: list[Batch], signals: set[str]) -> list[Batch]:
    """Keeps only the batches whose signal was selected, in arrival order.

    ``signals`` holds internal names (``logs``/``metrics``/``traces``). A capture
    that carries no batch for a selected signal simply yields fewer batches —
    selecting a signal never fabricates one.
    """
    return [b for b in batches if b.signal in signals]


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
            anchors = list(iter_timestamps(obj, anchor_keys_for(signal)))
            anchor_ns = min(anchors) if anchors else None
            batches.append(Batch(signal=signal, payload=obj, anchor_ns=anchor_ns))
    return batches
