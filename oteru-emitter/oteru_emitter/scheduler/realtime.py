"""Realtime scheduler: honors the original cadence between batches.

Spacing is computed from the batches' ORIGINAL ``anchor_ns`` (not the
re-stamped timestamps): since the restamp shifts everything by the same
offset, the relative deltas are identical. Long idle gaps are capped by
``max_gap`` so the replay doesn't stall for minutes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from ..sources.replay import Batch


def run_realtime(
    batches: Iterable[Batch],
    send: Callable[[Batch], None],
    *,
    max_gap: float = 5.0,
    speed: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Iterates the batches, sleeping the real delta (÷ speed, capped at
    max_gap) before each send. ``speed`` > 1 accelerates; 1.0 = realtime."""
    prev_anchor: int | None = None
    for batch in batches:
        if prev_anchor is not None and batch.anchor_ns is not None:
            delta_s = (batch.anchor_ns - prev_anchor) / 1e9
            delta_s = max(0.0, min(delta_s, max_gap))
            if delta_s > 0:
                sleep(delta_s / speed)
        send(batch)
        if batch.anchor_ns is not None:
            prev_anchor = batch.anchor_ns
