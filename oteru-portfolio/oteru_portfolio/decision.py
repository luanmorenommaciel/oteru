"""Owned decisions with estimates kept distinct from realized value."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True)
class FinancialEstimate:
    currency: str
    low: float
    expected: float
    high: float

    def __post_init__(self) -> None:
        if not self.currency or not 0 <= self.low <= self.expected <= self.high:
            raise ValueError("invalid financial interval")


@dataclass(frozen=True)
class RecommendedDecision:
    decision_id: str
    action: str
    owner: str
    due_date: date
    estimate: FinancialEstimate
    evidence_refs: tuple[str, ...]
    exception_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not all((self.decision_id, self.action, self.owner))
            or not self.evidence_refs
        ):
            raise ValueError("decision owner and evidence are required")

    def as_record(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "action": self.action,
            "owner": self.owner,
            "due_date": self.due_date.isoformat(),
            "financial_estimate": asdict(self.estimate),
            "financial_status": "estimated",
            "realized_value": None,
            "evidence_refs": list(self.evidence_refs),
            "exception_ref": self.exception_ref,
        }
