"""Versioned declared intent bound to a session or process."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DeclaredIntent:
    intent_id: str
    version: int
    spec_ref: str
    author: str
    declared_at: datetime
    execution_started_at: datetime
    session_id: str | None = None
    process_id: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if bool(self.session_id) == bool(self.process_id):
            raise ValueError("intent must bind to exactly one session or process")
        if self.version < 1 or not self.spec_ref or not self.author:
            raise ValueError("version, spec_ref and author are required")

    @property
    def available_at_start(self) -> bool:
        return self.declared_at <= self.execution_started_at

    @property
    def fingerprint(self) -> str:
        value = f"{self.intent_id}:{self.version}:{self.spec_ref}:{self.policy_version or ''}"
        return hashlib.sha256(value.encode()).hexdigest()


class IntentRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, list[DeclaredIntent]] = {}

    def add(self, intent: DeclaredIntent) -> None:
        versions = self._versions.setdefault(intent.intent_id, [])
        if versions and intent.version != versions[-1].version + 1:
            raise ValueError("intent versions must be contiguous and append-only")
        if not versions and intent.version != 1:
            raise ValueError("first intent version must be 1")
        versions.append(intent)

    def history(self, intent_id: str) -> tuple[DeclaredIntent, ...]:
        return tuple(self._versions.get(intent_id, []))
