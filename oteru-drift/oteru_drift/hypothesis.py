"""Falsifiable drift hypotheses linked to evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    evidence_ref: str
    polarity: str
    observation: str

    def __post_init__(self) -> None:
        if self.polarity not in {"for", "against"} or not self.evidence_ref:
            raise ValueError("evidence must be traceable and have a polarity")


@dataclass(frozen=True)
class DriftHypothesis:
    hypothesis_id: str
    version: int
    statement: str
    evidence: tuple[Evidence, ...]
    alternatives: tuple[str, ...]
    recommended_test: str
    test_result: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1 or not self.statement or not self.recommended_test:
            raise ValueError("a versioned, testable statement is required")
        if not self.alternatives:
            raise ValueError("at least one alternative explanation is required")
        if self.test_result not in {None, "supports", "refutes", "inconclusive"}:
            raise ValueError("invalid test result")


def evaluate_hypothesis(hypothesis: DriftHypothesis) -> dict[str, object]:
    for_refs = [
        item.evidence_ref for item in hypothesis.evidence if item.polarity == "for"
    ]
    against_refs = [
        item.evidence_ref for item in hypothesis.evidence if item.polarity == "against"
    ]
    status = {"supports": "confirmed", "refutes": "refuted"}.get(
        hypothesis.test_result, "unknown"
    )
    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "version": hypothesis.version,
        "status": status,
        "evidence_for": for_refs,
        "evidence_against": against_refs,
        "alternatives": list(hypothesis.alternatives),
        "recommended_test": hypothesis.recommended_test,
    }
