"""Local, privacy-conscious curation of recurring supported questions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .core import INTENTS


class QuestionLedger:
    """Persists intent counts and promotions, never the question or user identity."""

    def __init__(self, path: str | Path, *, threshold: int = 3):
        if threshold < 2:
            raise ValueError("curation threshold must be at least 2")
        self.path = Path(path)
        self.threshold = threshold
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS question_intents (
                    intent TEXT PRIMARY KEY,
                    asks INTEGER NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS indicators (
                    intent TEXT PRIMARY KEY,
                    promoted_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, intent: str) -> int:
        if intent not in INTENTS:
            raise ValueError("unsupported intent")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO question_intents(intent, asks, first_seen, last_seen)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(intent) DO UPDATE SET
                    asks = question_intents.asks + 1,
                    last_seen = excluded.last_seen
                """,
                (intent, now, now),
            )
            return int(
                db.execute(
                    "SELECT asks FROM question_intents WHERE intent = ?", (intent,)
                ).fetchone()[0]
            )

    def overview(self) -> dict:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT q.intent, q.asks, q.first_seen, q.last_seen, i.promoted_at
                FROM question_intents AS q
                LEFT JOIN indicators AS i ON i.intent = q.intent
                ORDER BY q.asks DESC, q.intent
                """
            ).fetchall()
        candidates = []
        promoted = []
        for row in rows:
            item = {
                "intent": row["intent"],
                "title": INTENTS[row["intent"]]["title"],
                "asks": row["asks"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
            if row["promoted_at"]:
                item["promoted_at"] = row["promoted_at"]
                promoted.append(item)
            elif row["asks"] >= self.threshold:
                candidates.append(item)
        return {"threshold": self.threshold, "candidates": candidates, "promoted": promoted}

    def promote(self, intent: str) -> bool:
        if intent not in INTENTS:
            raise ValueError("unsupported intent")
        with self._connect() as db:
            row = db.execute(
                "SELECT asks FROM question_intents WHERE intent = ?", (intent,)
            ).fetchone()
            if row is None or row["asks"] < self.threshold:
                return False
            db.execute(
                "INSERT OR IGNORE INTO indicators(intent, promoted_at) VALUES (?, ?)",
                (intent, datetime.now(timezone.utc).isoformat()),
            )
        return True

    def remove(self, intent: str) -> bool:
        if intent not in INTENTS:
            raise ValueError("unsupported intent")
        with self._connect() as db:
            return bool(db.execute("DELETE FROM indicators WHERE intent = ?", (intent,)).rowcount)
