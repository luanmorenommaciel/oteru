"""Testes de ``oteru_emitter.scheduler.realtime`` (pacing pelos deltas originais)."""

from __future__ import annotations

from oteru_emitter.scheduler.realtime import run_realtime
from oteru_emitter.sources.replay import Batch

S = 1_000_000_000  # 1 s em ns


def _batch(anchor_ns: int | None) -> Batch:
    return Batch(signal="logs", payload={}, anchor_ns=anchor_ns)


def _run(batches, **kwargs):
    sleeps: list[float] = []
    enviadas: list[Batch] = []
    run_realtime(batches, enviadas.append, sleep=sleeps.append, **kwargs)
    return sleeps, enviadas


def test_sem_sleep_antes_da_primeira_batch():
    sleeps, _ = _run([_batch(10 * S)], max_gap=60)
    assert sleeps == []


def test_deltas_originais():
    sleeps, _ = _run([_batch(0), _batch(3 * S), _batch(7 * S)], max_gap=60)
    assert sleeps == [3.0, 4.0]


def test_teto_max_gap():
    sleeps, _ = _run([_batch(0), _batch(120 * S)], max_gap=5.0)
    assert sleeps == [5.0]


def test_speed_divide_o_delta():
    sleeps, _ = _run([_batch(0), _batch(4 * S)], max_gap=60, speed=4.0)
    assert sleeps == [1.0]


def test_batch_sem_anchor_nao_avanca_o_relogio():
    sleeps, enviadas = _run([_batch(0), _batch(None), _batch(3 * S)], max_gap=60)
    # nada de sleep antes da batch sem anchor; o delta seguinte ainda parte do 0
    assert sleeps == [3.0]
    assert len(enviadas) == 3


def test_send_uma_vez_por_batch_em_ordem():
    batches = [_batch(0), _batch(S), _batch(2 * S)]
    _, enviadas = _run(batches, max_gap=60)
    assert enviadas == batches
