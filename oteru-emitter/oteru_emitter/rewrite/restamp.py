"""Restamp: re-stamps a capture for re-sending, **preserving the structure**.

Faithful replay does not mean blind replay: to re-send the same capture many
times without the backend deduplicating it, we swap the *identity* (timestamps
and per-run correlation IDs) while keeping the *structure* intact — attribute
types, ordering, redundancies (`cost_usd` + `cost_usd_micros`) all stay the
same.

- Timestamps: all shifted by the same offset so the oldest event in the
  capture lands at ~"now". Durations and spacing are preserved.
- Correlation IDs (e.g. session.id, prompt.id, request_id): rotated
  consistently — the same old value always maps to the same new value within
  a run, so grouping by session/turn is preserved.
- Principal identity (user.id, user.email, organization.id) is NOT touched:
  it is a faithful part of the data, not per-run correlation.
"""

from __future__ import annotations

import random
import string
import time
import uuid
from collections.abc import Callable, Iterable

from ..sources.replay import Batch

# Every timestamp to shift. start/endTimeUnixNano are both here so durations
# survive: shifting only the start of a span would leave end < start and yield
# a negative duration downstream.
SHIFT_TIME_KEYS = {
    "timeUnixNano",
    "observedTimeUnixNano",
    "startTimeUnixNano",
    "endTimeUnixNano",
}

_ID_ALPHABET = string.ascii_letters + string.digits

IdGen = Callable[[str, str], str]


def _shift_timestamps(node: object, offset_ns: int) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in SHIFT_TIME_KEYS and isinstance(value, (str, int)):
                try:
                    node[key] = str(int(value) + offset_ns)
                    continue
                except (TypeError, ValueError):
                    pass
            _shift_timestamps(value, offset_ns)
    elif isinstance(node, list):
        for item in node:
            _shift_timestamps(item, offset_ns)


def _rotate_ids(
    node: object,
    keys: set[str],
    mapping: dict[tuple[str, str], str],
    gen: IdGen,
) -> None:
    if isinstance(node, dict):
        # OTLP attribute entry: {"key": <str>, "value": {"stringValue": ...}}
        attr_key = node.get("key")
        value = node.get("value")
        if (
            attr_key in keys
            and isinstance(value, dict)
            and isinstance(value.get("stringValue"), str)
        ):
            old = value["stringValue"]
            map_key = (attr_key, old)
            if map_key not in mapping:
                mapping[map_key] = gen(attr_key, old)
            value["stringValue"] = mapping[map_key]
        for child in node.values():
            _rotate_ids(child, keys, mapping, gen)
    elif isinstance(node, list):
        for item in node:
            _rotate_ids(item, keys, mapping, gen)


def _make_id_gen(rnd: random.Random) -> IdGen:
    """Generates new IDs preserving the original's *format* (seedable)."""

    def gen(_key: str, old: str) -> str:
        if old.startswith("req_"):
            # e.g. req_011CbtJR1bZ4uLs3hmdd96uE
            n = max(len(old) - 4, 8)
            return "req_" + "".join(rnd.choice(_ID_ALPHABET) for _ in range(n))
        # default: UUID v4 (covers session.id, prompt.id and the like)
        return str(uuid.UUID(int=rnd.getrandbits(128), version=4))

    return gen


def restamp(
    batches: Iterable[Batch],
    *,
    shift_time: bool = True,
    rotate_keys: Iterable[str] | None = None,
    seed: int | None = None,
    now_ns: int | None = None,
) -> int:
    """Applies the restamp in-place. Returns the time offset applied (ns)."""
    batches = list(batches)
    offset_ns = 0

    if shift_time:
        anchors = [b.anchor_ns for b in batches if b.anchor_ns is not None]
        if anchors:
            now_ns = now_ns if now_ns is not None else time.time_ns()
            offset_ns = now_ns - min(anchors)
            for batch in batches:
                _shift_timestamps(batch.payload, offset_ns)

    rotate_set = {k for k in (rotate_keys or [])}
    if rotate_set:
        rnd = random.Random(seed)
        gen = _make_id_gen(rnd)
        mapping: dict[tuple[str, str], str] = {}
        for batch in batches:
            _rotate_ids(batch.payload, rotate_set, mapping, gen)

    return offset_ns
