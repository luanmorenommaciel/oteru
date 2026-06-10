"""Testes de integração com o sample real commitado (523 batches, PII redigida)."""

from __future__ import annotations

import json

import pytest

from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import load_batches

ROTATE = ("session.id", "prompt.id", "request_id")
NOW_NS = 2_000_000_000_000_000_000


def test_contagem_do_sample(sample_path):
    batches = load_batches(str(sample_path))
    assert len(batches) == 523
    assert sum(1 for b in batches if b.signal == "logs") == 348
    assert sum(1 for b in batches if b.signal == "metrics") == 175


def test_restamp_seedado_deterministico(sample_path):
    a = load_batches(str(sample_path))
    b = load_batches(str(sample_path))
    restamp(a, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    restamp(b, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    assert [json.dumps(x.payload, sort_keys=True) for x in a] == [
        json.dumps(x.payload, sort_keys=True) for x in b
    ]


def test_rotacao_de_sessoes_e_bijetiva(sample_path, attr_values):
    batches = load_batches(str(sample_path))
    antigos: set[str] = set()
    for b in batches:
        antigos.update(attr_values(b.payload, "session.id"))
    assert len(antigos) == 21

    restamp(batches, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    novos: set[str] = set()
    for b in batches:
        novos.update(attr_values(b.payload, "session.id"))
    assert len(novos) == 21  # bijeção: 21 distintos -> 21 distintos
    assert not novos & antigos  # nenhum valor antigo sobrevive


def test_email_redigido_preservado_em_todo_lugar(sample_path, attr_values):
    batches = load_batches(str(sample_path))
    restamp(batches, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    total = 0
    for b in batches:
        emails = attr_values(b.payload, "user.email")
        total += len(emails)
        assert set(emails) <= {"user@example.com"}
    assert total > 0


def test_todas_as_batches_convertem_a_protobuf(sample_path):
    pytest.importorskip("opentelemetry.proto")
    from oteru_emitter.model.otlp import to_request

    for batch in load_batches(str(sample_path)):
        request = to_request(batch.signal, batch.payload)
        assert request.ByteSize() > 0
