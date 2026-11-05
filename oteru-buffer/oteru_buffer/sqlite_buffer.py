"""SQLite reference implementation of ADR-002's durable queue contract."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BufferedEvent:
    event_id: str
    tenant_id: str
    signal_type: str
    payload: bytes
    payload_digest: str
    attempts: int


class SQLiteBuffer:
    def __init__(self, path: Path, *, max_attempts: int = 5):
        self.path = path
        self.max_attempts = max_attempts
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                payload BLOB NOT NULL,
                payload_digest TEXT NOT NULL,
                received_at REAL NOT NULL,
                available_at REAL NOT NULL,
                lease_until REAL,
                state TEXT NOT NULL CHECK(state IN ('queued','leased','dlq')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """
        )

    def close(self) -> None:
        self.connection.close()

    def enqueue(
        self, event_id: str, tenant_id: str, signal_type: str, payload: bytes
    ) -> bool:
        if (
            not event_id
            or not tenant_id
            or signal_type not in {"logs", "metrics", "traces"}
        ):
            raise ValueError("event_id, tenant_id and a valid signal_type are required")
        digest = hashlib.sha256(payload).hexdigest()
        now = time.time()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT payload_digest FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row:
                if row["payload_digest"] != digest:
                    raise ValueError("event_id already exists with different payload")
                self.connection.execute("COMMIT")
                return False
            self.connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'queued', 0, NULL)",
                (event_id, tenant_id, signal_type, payload, digest, now, now),
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def lease(
        self, *, limit: int = 100, lease_seconds: float = 30
    ) -> list[BufferedEvent]:
        if limit < 1 or lease_seconds <= 0:
            raise ValueError("limit and lease_seconds must be positive")
        now = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                """
                SELECT * FROM events
                WHERE (state = 'queued' AND available_at <= ?)
                   OR (state = 'leased' AND lease_until <= ?)
                ORDER BY received_at, event_id LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            for row in rows:
                self.connection.execute(
                    "UPDATE events SET state='leased', lease_until=?, attempts=attempts+1 WHERE event_id=?",
                    (now + lease_seconds, row["event_id"]),
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return [
            BufferedEvent(
                row["event_id"],
                row["tenant_id"],
                row["signal_type"],
                bytes(row["payload"]),
                row["payload_digest"],
                row["attempts"] + 1,
            )
            for row in rows
        ]

    def ack(self, event_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM events WHERE event_id = ? AND state = 'leased'", (event_id,)
        )
        return cursor.rowcount == 1

    def nack(self, event_id: str, error: str, *, retry_after: float = 0) -> str:
        row = self.connection.execute(
            "SELECT attempts FROM events WHERE event_id = ? AND state = 'leased'",
            (event_id,),
        ).fetchone()
        if not row:
            raise KeyError(event_id)
        state = "dlq" if row["attempts"] >= self.max_attempts else "queued"
        self.connection.execute(
            "UPDATE events SET state=?, available_at=?, lease_until=NULL, last_error=? WHERE event_id=?",
            (state, time.time() + retry_after, error[:500], event_id),
        )
        return state

    def stats(self) -> dict[str, int]:
        result = {"queued": 0, "leased": 0, "dlq": 0}
        for row in self.connection.execute(
            "SELECT state, COUNT(*) AS count FROM events GROUP BY state"
        ):
            result[row["state"]] = row["count"]
        return result
