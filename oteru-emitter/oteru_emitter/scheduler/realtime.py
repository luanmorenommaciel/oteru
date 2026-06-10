"""Scheduler de tempo real: honra a cadência original entre batches.

O espaçamento é calculado a partir dos ``anchor_ns`` ORIGINAIS das batches
(não dos timestamps re-carimbados): como o restamp desloca tudo pelo mesmo
offset, os deltas relativos são idênticos. Gaps ociosos longos são limitados
por ``max_gap`` para não travar o replay por minutos.
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
    """Itera as batches, dormindo o delta real (÷ speed, teto max_gap) antes de
    cada envio. ``speed`` > 1 acelera; 1.0 = tempo real."""
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
