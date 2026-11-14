import time
from pathlib import Path

import pytest
from oteru_buffer import SQLiteBuffer


def open_buffer(path: Path, max_attempts: int = 5) -> SQLiteBuffer:
    return SQLiteBuffer(path, max_attempts=max_attempts)


def test_acknowledged_enqueue_survives_process_reopen(tmp_path: Path) -> None:
    path = tmp_path / "buffer.db"
    first = open_buffer(path)
    assert first.enqueue("event-1", "tenant-a", "logs", b"payload") is True
    first.close()

    second = open_buffer(path)
    leased = second.lease(limit=1)
    assert leased[0].event_id == "event-1"
    assert leased[0].payload == b"payload"
    assert second.ack("event-1") is True
    assert second.stats() == {"queued": 0, "leased": 0, "dlq": 0}
    second.close()


def test_event_id_is_idempotent_but_rejects_different_payload(tmp_path: Path) -> None:
    buffer = open_buffer(tmp_path / "buffer.db")
    assert buffer.enqueue("same", "tenant-a", "traces", b"one") is True
    assert buffer.enqueue("same", "tenant-a", "traces", b"one") is False
    with pytest.raises(ValueError, match="different payload"):
        buffer.enqueue("same", "tenant-a", "traces", b"two")
    buffer.close()


def test_expired_lease_is_recovered_and_failed_event_reaches_dlq(
    tmp_path: Path,
) -> None:
    buffer = open_buffer(tmp_path / "buffer.db", max_attempts=2)
    buffer.enqueue("event", "tenant", "metrics", b"data")
    assert buffer.lease(lease_seconds=0.01)[0].attempts == 1
    time.sleep(0.02)
    assert buffer.lease()[0].attempts == 2
    assert buffer.nack("event", "backend unavailable") == "dlq"
    assert buffer.stats()["dlq"] == 1
    buffer.close()
