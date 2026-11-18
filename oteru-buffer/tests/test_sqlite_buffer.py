import sqlite3
import time
from pathlib import Path

import pytest

from oteru_buffer import SQLiteBuffer

KEY = b"k" * 32


def open_buffer(path: Path, max_attempts: int = 5) -> SQLiteBuffer:
    return SQLiteBuffer(path, encryption_key=KEY, max_attempts=max_attempts)


def test_acknowledged_enqueue_survives_process_reopen(tmp_path: Path) -> None:
    path = tmp_path / "buffer.db"
    first = open_buffer(path)
    assert first.enqueue("event-1", "tenant-a", "logs", b"payload") is True
    first.close()

    second = open_buffer(path)
    leased = second.lease(limit=1)[0]
    assert leased.payload == b"payload"
    assert second.ack(leased.tenant_id, leased.event_id, leased.lease_token) is True
    assert second.enqueue("event-1", "tenant-a", "logs", b"payload") is False
    second.close()


def test_deduplication_is_scoped_by_tenant(tmp_path: Path) -> None:
    buffer = open_buffer(tmp_path / "buffer.db")
    assert buffer.enqueue("same", "tenant-a", "traces", b"one") is True
    assert buffer.enqueue("same", "tenant-b", "traces", b"one") is True
    assert buffer.enqueue("same", "tenant-a", "traces", b"one") is False
    with pytest.raises(ValueError, match="different payload"):
        buffer.enqueue("same", "tenant-a", "traces", b"two")
    buffer.close()


def test_expired_lease_rejects_ack_and_moves_poison_to_dlq(tmp_path: Path) -> None:
    buffer = open_buffer(tmp_path / "buffer.db", max_attempts=1)
    buffer.enqueue("event", "tenant", "metrics", b"data")
    leased = buffer.lease(lease_seconds=0.01)[0]
    time.sleep(0.02)
    assert buffer.ack("tenant", "event", leased.lease_token) is False
    assert buffer.lease() == []
    assert buffer.stats()["dlq"] == 1
    buffer.close()


def test_stale_consumer_cannot_ack_released_event(tmp_path: Path) -> None:
    buffer = open_buffer(tmp_path / "buffer.db", max_attempts=2)
    buffer.enqueue("event", "tenant", "logs", b"data")
    stale = buffer.lease(lease_seconds=0.01)[0]
    time.sleep(0.02)
    current = buffer.lease()[0]
    assert buffer.ack("tenant", "event", stale.lease_token) is False
    assert buffer.ack("tenant", "event", current.lease_token) is True
    buffer.close()


def test_payload_is_encrypted_at_rest(tmp_path: Path) -> None:
    path = tmp_path / "buffer.db"
    payload = b"plaintext-email@example.com"
    buffer = open_buffer(path)
    buffer.enqueue("event", "tenant", "logs", payload)
    buffer.close()
    raw = sqlite3.connect(path)
    row = raw.execute("SELECT payload_ciphertext, payload_nonce FROM events").fetchone()
    raw.close()
    assert payload not in row[0]
    assert len(row[1]) == 12


def test_nack_requires_current_lease_token(tmp_path: Path) -> None:
    buffer = open_buffer(tmp_path / "buffer.db")
    buffer.enqueue("event", "tenant", "logs", b"data")
    leased = buffer.lease()[0]
    with pytest.raises(KeyError):
        buffer.nack("tenant", "event", "wrong", "error")
    assert (
        buffer.nack("tenant", "event", leased.lease_token, "backend unavailable")
        == "queued"
    )
    buffer.close()
