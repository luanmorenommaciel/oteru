"""SQLite reference implementation of ADR-002's durable queue contract."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class BufferedEvent:
    event_id: str
    tenant_id: str
    signal_type: str
    payload: bytes
    payload_digest: str
    attempts: int
    lease_token: str


class SQLiteBuffer:
    def __init__(
        self, path: Path, *, encryption_key: bytes, max_attempts: int = 5
    ) -> None:
        if len(encryption_key) != 32:
            raise ValueError("encryption_key must contain exactly 32 bytes")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        self._cipher = AESGCM(encryption_key)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                tenant_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                payload_nonce BLOB NOT NULL,
                payload_ciphertext BLOB NOT NULL,
                payload_digest TEXT NOT NULL,
                received_at REAL NOT NULL,
                available_at REAL NOT NULL,
                lease_until REAL,
                lease_token TEXT,
                state TEXT NOT NULL CHECK(state IN ('queued','leased','dlq')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY (tenant_id, event_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS acknowledgements (
                tenant_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                acknowledged_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, event_id)
            )
            """
        )

    def close(self) -> None:
        self.connection.close()

    def _encrypt(
        self, tenant_id: str, event_id: str, payload: bytes
    ) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        aad = f"{tenant_id}\0{event_id}".encode()
        return nonce, self._cipher.encrypt(nonce, payload, aad)

    def _decrypt(self, row: sqlite3.Row) -> bytes:
        aad = f"{row['tenant_id']}\0{row['event_id']}".encode()
        return self._cipher.decrypt(
            bytes(row["payload_nonce"]), bytes(row["payload_ciphertext"]), aad
        )

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
                "SELECT payload_digest FROM events WHERE tenant_id=? AND event_id=?",
                (tenant_id, event_id),
            ).fetchone()
            if not row:
                row = self.connection.execute(
                    """
                    SELECT payload_digest FROM acknowledgements
                    WHERE tenant_id=? AND event_id=?
                    """,
                    (tenant_id, event_id),
                ).fetchone()
            if row:
                if row["payload_digest"] != digest:
                    raise ValueError("event_id already exists with different payload")
                self.connection.execute("COMMIT")
                return False
            nonce, ciphertext = self._encrypt(tenant_id, event_id, payload)
            self.connection.execute(
                """
                INSERT INTO events VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'queued', 0, NULL
                )
                """,
                (
                    tenant_id,
                    event_id,
                    signal_type,
                    nonce,
                    ciphertext,
                    digest,
                    now,
                    now,
                ),
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
            self.connection.execute(
                """
                UPDATE events
                SET state='dlq', lease_until=NULL, lease_token=NULL,
                    last_error=COALESCE(last_error, 'lease expired after max attempts')
                WHERE state='leased' AND lease_until<=? AND attempts>=?
                """,
                (now, self.max_attempts),
            )
            rows = self.connection.execute(
                """
                SELECT * FROM events
                WHERE attempts<? AND (
                    (state='queued' AND available_at<=?)
                    OR (state='leased' AND lease_until<=?)
                )
                ORDER BY received_at, tenant_id, event_id LIMIT ?
                """,
                (self.max_attempts, now, now, limit),
            ).fetchall()
            leased = []
            for row in rows:
                token = secrets.token_urlsafe(24)
                self.connection.execute(
                    """
                    UPDATE events
                    SET state='leased', lease_until=?, lease_token=?, attempts=attempts+1
                    WHERE tenant_id=? AND event_id=?
                    """,
                    (now + lease_seconds, token, row["tenant_id"], row["event_id"]),
                )
                leased.append(
                    BufferedEvent(
                        row["event_id"],
                        row["tenant_id"],
                        row["signal_type"],
                        self._decrypt(row),
                        row["payload_digest"],
                        row["attempts"] + 1,
                        token,
                    )
                )
            self.connection.execute("COMMIT")
            return leased
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def ack(self, tenant_id: str, event_id: str, lease_token: str) -> bool:
        now = time.time()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT payload_digest FROM events
                WHERE tenant_id=? AND event_id=? AND state='leased'
                  AND lease_token=? AND lease_until>?
                """,
                (tenant_id, event_id, lease_token, now),
            ).fetchone()
            if not row:
                self.connection.execute("ROLLBACK")
                return False
            self.connection.execute(
                "INSERT INTO acknowledgements VALUES (?, ?, ?, ?)",
                (tenant_id, event_id, row["payload_digest"], now),
            )
            self.connection.execute(
                "DELETE FROM events WHERE tenant_id=? AND event_id=?",
                (tenant_id, event_id),
            )
            self.connection.execute("COMMIT")
            return True
        except Exception:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def nack(
        self,
        tenant_id: str,
        event_id: str,
        lease_token: str,
        error: str,
        *,
        retry_after: float = 0,
    ) -> str:
        now = time.time()
        row = self.connection.execute(
            """
            SELECT attempts FROM events
            WHERE tenant_id=? AND event_id=? AND state='leased'
              AND lease_token=? AND lease_until>?
            """,
            (tenant_id, event_id, lease_token, now),
        ).fetchone()
        if not row:
            raise KeyError((tenant_id, event_id))
        state = "dlq" if row["attempts"] >= self.max_attempts else "queued"
        self.connection.execute(
            """
            UPDATE events
            SET state=?, available_at=?, lease_until=NULL, lease_token=NULL, last_error=?
            WHERE tenant_id=? AND event_id=?
            """,
            (state, now + retry_after, error[:500], tenant_id, event_id),
        )
        return state

    def stats(self) -> dict[str, int]:
        result = {"queued": 0, "leased": 0, "dlq": 0}
        for row in self.connection.execute(
            "SELECT state, COUNT(*) AS count FROM events GROUP BY state"
        ):
            result[row["state"]] = row["count"]
        return result
