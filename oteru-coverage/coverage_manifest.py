"""Load and validate Oteru's explicit coverage claims."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class CoverageKind(str, Enum):
    REAL = "real"
    SYNTHETIC = "synthetic"
    INFERRED = "inferred"
    CONCEPTUAL = "conceptual"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CoverageEntry:
    feature: str
    kind: CoverageKind
    source: str
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]


def load_manifest(path: Path) -> list[CoverageEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise ValueError("unsupported coverage manifest version")
    entries: list[CoverageEntry] = []
    seen: set[str] = set()
    for item in raw.get("entries", []):
        feature = str(item.get("feature", "")).strip()
        if not feature or feature in seen:
            raise ValueError(f"missing or duplicate feature: {feature!r}")
        seen.add(feature)
        kind = CoverageKind(item.get("kind"))
        source = str(item.get("source", "")).strip()
        evidence = tuple(str(value) for value in item.get("evidence", []))
        limitations = tuple(str(value) for value in item.get("limitations", []))
        if not source or not limitations:
            raise ValueError(f"{feature}: source and limitations are required")
        if (
            kind in {CoverageKind.REAL, CoverageKind.SYNTHETIC, CoverageKind.INFERRED}
            and not evidence
        ):
            raise ValueError(f"{feature}: {kind.value} coverage requires evidence")
        entries.append(CoverageEntry(feature, kind, source, evidence, limitations))
    if not entries:
        raise ValueError("coverage manifest is empty")
    return entries


def public_view(entries: list[CoverageEntry]) -> list[dict]:
    """Single UI/API representation; callers must not relabel the coverage kind."""
    return [
        {
            "feature": entry.feature,
            "coverage": entry.kind.value,
            "source": entry.source,
            "evidence": list(entry.evidence),
            "limitations": list(entry.limitations),
        }
        for entry in entries
    ]
