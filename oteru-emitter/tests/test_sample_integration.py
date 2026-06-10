"""Integration tests against the committed real sample (523 batches, PII redacted)."""

from __future__ import annotations

import json

import pytest

from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import load_batches

ROTATE = ("session.id", "prompt.id", "request_id")
NOW_NS = 2_000_000_000_000_000_000


def test_sample_counts(sample_path):
    batches = load_batches(str(sample_path))
    assert len(batches) == 523
    assert sum(1 for b in batches if b.signal == "logs") == 348
    assert sum(1 for b in batches if b.signal == "metrics") == 175


def test_seeded_restamp_is_deterministic(sample_path):
    a = load_batches(str(sample_path))
    b = load_batches(str(sample_path))
    restamp(a, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    restamp(b, rotate_keys=ROTATE, seed=42, now_ns=NOW_NS)
    assert [json.dumps(x.payload, sort_keys=True) for x in a] == [
        json.dumps(x.payload, sort_keys=True) for x in b
    ]


def test_session_rotation_is_bijective(sample_path, attr_values):
    batches = load_batches(str(sample_path))
    old_values: set[str] = set()
    for b in batches:
        old_values.update(attr_values(b.payload, "session.id"))
    assert len(old_values) == 21

    restamp(batches, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    new_values: set[str] = set()
    for b in batches:
        new_values.update(attr_values(b.payload, "session.id"))
    assert len(new_values) == 21  # bijection: 21 distinct -> 21 distinct
    assert not new_values & old_values  # no old value survives


def test_redacted_email_preserved_everywhere(sample_path, attr_values):
    batches = load_batches(str(sample_path))
    restamp(batches, rotate_keys=ROTATE, seed=7, now_ns=NOW_NS)
    total = 0
    for b in batches:
        emails = attr_values(b.payload, "user.email")
        total += len(emails)
        assert set(emails) <= {"user@example.com"}
    assert total > 0


def test_every_batch_converts_to_protobuf(sample_path):
    pytest.importorskip("opentelemetry.proto")
    from oteru_emitter.model.otlp import to_request

    for batch in load_batches(str(sample_path)):
        request = to_request(batch.signal, batch.payload)
        assert request.ByteSize() > 0
