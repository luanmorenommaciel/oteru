"""Append-only provenance for links between evidence artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ProvenanceKind(str, Enum):
    DECLARED = "declared"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class ProvenanceTransition:
    previous: ProvenanceKind
    current: ProvenanceKind
    actor: str
    occurred_at: datetime
    evidence_ref: str
    rule_version: str | None = None


ALLOWED = {
    ProvenanceKind.UNCLASSIFIED: {
        ProvenanceKind.DECLARED,
        ProvenanceKind.INFERRED,
        ProvenanceKind.CONFIRMED,
    },
    ProvenanceKind.DECLARED: {ProvenanceKind.CONFIRMED},
    ProvenanceKind.INFERRED: {ProvenanceKind.CONFIRMED},
    ProvenanceKind.CONFIRMED: set(),
}


@dataclass
class LinkRecord:
    link_id: str
    source_ref: str
    target_ref: str
    current: ProvenanceKind = ProvenanceKind.UNCLASSIFIED
    history: list[ProvenanceTransition] = field(default_factory=list)

    def transition(
        self,
        current: ProvenanceKind,
        *,
        actor: str,
        evidence_ref: str,
        rule_version: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ProvenanceTransition:
        if current not in ALLOWED[self.current]:
            raise ValueError(
                f"invalid provenance transition: {self.current.value} -> {current.value}"
            )
        if not actor or not evidence_ref:
            raise ValueError("actor and evidence_ref are required")
        transition = ProvenanceTransition(
            self.current,
            current,
            actor,
            occurred_at or datetime.now(timezone.utc),
            evidence_ref,
            rule_version,
        )
        self.history.append(transition)
        self.current = current
        return transition
