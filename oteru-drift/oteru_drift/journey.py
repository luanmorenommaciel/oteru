"""Reconstruct session-to-delivery journeys without inventing links."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

ARTIFACT_KINDS = {
    "session",
    "spec",
    "implementation",
    "commit",
    "pull_request",
    "issue",
}
PROVENANCE = {"declared", "inferred", "confirmed", "unclassified"}


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    version: str
    observed_at: datetime
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS or self.observed_at.tzinfo is None:
            raise ValueError("invalid artifact")
        if not all((self.artifact_id, self.version, self.evidence_ref)):
            raise ValueError("artifact identity and evidence are required")


@dataclass(frozen=True)
class ArtifactLink:
    source_id: str
    target_id: str
    provenance: str
    evidence_ref: str | None = None
    actor_id: str | None = None

    def __post_init__(self) -> None:
        if self.provenance not in PROVENANCE:
            raise ValueError("invalid provenance")
        if self.provenance in {"declared", "confirmed"} and not self.evidence_ref:
            raise ValueError("declared and confirmed links require evidence")
        if self.provenance in {"inferred", "unclassified"} and self.actor_id:
            raise ValueError("inferred links cannot assert authorship")


def reconstruct_journey(
    artifacts: list[Artifact], links: list[ArtifactLink]
) -> dict[str, object]:
    artifact_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    if len(artifact_by_id) != len(artifacts):
        raise ValueError("duplicate artifact")
    gaps: list[str] = []
    for link in links:
        if link.source_id not in artifact_by_id or link.target_id not in artifact_by_id:
            gaps.append(f"unresolved:{link.source_id}->{link.target_id}")
        if link.provenance == "unclassified":
            gaps.append(f"unclassified:{link.source_id}->{link.target_id}")
    present = {artifact.kind for artifact in artifacts}
    for kind in ARTIFACT_KINDS:
        if kind not in present:
            gaps.append(f"missing:{kind}")
    return {
        "artifacts": [
            artifact.artifact_id
            for artifact in sorted(artifacts, key=lambda item: item.observed_at)
        ],
        "links": [
            {
                "source": link.source_id,
                "target": link.target_id,
                "provenance": link.provenance,
            }
            for link in links
        ],
        "gaps": sorted(gaps),
    }
