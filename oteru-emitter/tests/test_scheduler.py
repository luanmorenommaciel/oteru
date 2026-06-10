"""Tests for ``oteru_emitter.scheduler.realtime`` (pacing by original deltas)."""

from __future__ import annotations

from oteru_emitter.scheduler.realtime import run_realtime
from oteru_emitter.sources.replay import Batch

S = 1_000_000_000  # 1 s in ns


def _batch(anchor_ns: int | None) -> Batch:
    return Batch(signal="logs", payload={}, anchor_ns=anchor_ns)


def _run(batches, **kwargs):
    sleeps: list[float] = []
    sent: list[Batch] = []
    run_realtime(batches, sent.append, sleep=sleeps.append, **kwargs)
    return sleeps, sent


def test_no_sleep_before_first_batch():
    sleeps, _ = _run([_batch(10 * S)], max_gap=60)
    assert sleeps == []


def test_original_deltas():
    sleeps, _ = _run([_batch(0), _batch(3 * S), _batch(7 * S)], max_gap=60)
    assert sleeps == [3.0, 4.0]


def test_max_gap_cap():
    sleeps, _ = _run([_batch(0), _batch(120 * S)], max_gap=5.0)
    assert sleeps == [5.0]


def test_speed_divides_the_delta():
    sleeps, _ = _run([_batch(0), _batch(4 * S)], max_gap=60, speed=4.0)
    assert sleeps == [1.0]


def test_batch_without_anchor_does_not_advance_clock():
    sleeps, sent = _run([_batch(0), _batch(None), _batch(3 * S)], max_gap=60)
    # no sleep before the anchorless batch; the next delta still counts from 0
    assert sleeps == [3.0]
    assert len(sent) == 3


def test_send_called_once_per_batch_in_order():
    batches = [_batch(0), _batch(S), _batch(2 * S)]
    _, sent = _run(batches, max_gap=60)
    assert sent == batches
