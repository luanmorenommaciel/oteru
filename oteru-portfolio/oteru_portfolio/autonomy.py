"""Temporal autonomy and observed coverage per process."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

LEVELS = {"manual", "assisted", "supervised", "autonomous"}


@dataclass(frozen=True)
class AutonomyObservation:
    process_id: str
    valid_from: datetime
    declared_level: str
    observed_level: str | None
    coverage_ratio: float | None
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.valid_from.tzinfo is None or self.declared_level not in LEVELS:
            raise ValueError("invalid autonomy observation")
        if self.observed_level is not None and self.observed_level not in LEVELS:
            raise ValueError("invalid observed level")
        if self.coverage_ratio is not None and not 0 <= self.coverage_ratio <= 1:
            raise ValueError("coverage must be normalized")
        if not self.evidence_refs:
            raise ValueError("evidence is required")


def build_autonomy_history(
    observations: list[AutonomyObservation],
) -> list[dict[str, object]]:
    seen: set[tuple[str, datetime]] = set()
    rows = []
    for observation in sorted(
        observations, key=lambda item: (item.process_id, item.valid_from)
    ):
        key = (observation.process_id, observation.valid_from)
        if key in seen:
            raise ValueError("duplicate process observation")
        seen.add(key)
        rows.append(
            {
                "process_id": observation.process_id,
                "valid_from": observation.valid_from.isoformat(),
                "declared_level": observation.declared_level,
                "observed_level": observation.observed_level or "unknown",
                "coverage_ratio": observation.coverage_ratio,
                "coverage_known": observation.coverage_ratio is not None,
                "evidence_refs": list(observation.evidence_refs),
            }
        )
    return rows
