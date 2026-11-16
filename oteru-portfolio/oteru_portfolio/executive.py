"""Non-technical executive claims with traceable scope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ExecutiveClaim:
    text: str
    source: str
    period_start: date
    period_end: date
    evidence_ref: str
    limitation: str

    def __post_init__(self) -> None:
        if self.period_start > self.period_end:
            raise ValueError("invalid claim period")
        if not all((self.text, self.source, self.evidence_ref, self.limitation)):
            raise ValueError("claim provenance and limitation are required")


def build_executive_report(claims: list[ExecutiveClaim]) -> dict[str, object]:
    if not claims:
        raise ValueError("at least one claim is required")
    return {
        "claims": [
            {
                "text": claim.text,
                "source": claim.source,
                "period": {
                    "start": claim.period_start.isoformat(),
                    "end": claim.period_end.isoformat(),
                },
                "evidence_ref": claim.evidence_ref,
                "limitation": claim.limitation,
                "drill_down": f"evidence/{claim.evidence_ref}",
            }
            for claim in claims
        ],
        "claim_count": len(claims),
    }
