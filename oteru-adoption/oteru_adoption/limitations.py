"""Make unsupported interpretations explicit in every adoption view."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PROHIBITED_CLAIMS = {
    "productivity",
    "presence",
    "authorship",
    "undeclared_intent",
    "complete_observation",
}


@dataclass(frozen=True)
class ClaimContext:
    period_start: datetime
    period_end: datetime
    coverage_ratio: float
    sources: tuple[str, ...]
    limitations: tuple[str, ...]
    contains_synthetic: bool = False

    def __post_init__(self) -> None:
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("period must be timezone-aware")
        if self.period_start >= self.period_end:
            raise ValueError("invalid period")
        if not 0 <= self.coverage_ratio <= 1:
            raise ValueError("coverage_ratio must be between zero and one")
        if not self.sources or not self.limitations:
            raise ValueError("sources and limitations are required")


def evaluate_claim(claim_kind: str, context: ClaimContext) -> dict[str, object]:
    """Return a reusable disclosure and whether the claim is authorized."""
    reasons = list(context.limitations)
    allowed = claim_kind not in PROHIBITED_CLAIMS
    if not allowed:
        reasons.append(f"{claim_kind} cannot be inferred from operational activity")
    if context.coverage_ratio < 1:
        reasons.append("the observed period has incomplete coverage")
    if context.contains_synthetic:
        reasons.append("synthetic observations are present and identified separately")
    return {
        "allowed": allowed,
        "claim_kind": claim_kind,
        "period_start": context.period_start.isoformat(),
        "period_end": context.period_end.isoformat(),
        "coverage_ratio": context.coverage_ratio,
        "sources": list(context.sources),
        "limitations": reasons,
    }
