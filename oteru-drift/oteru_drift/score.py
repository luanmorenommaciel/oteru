"""Versioned, evidence-bound adherence scores."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rubric:
    version: str
    weights: dict[str, float]
    golden_dataset: str
    evaluator_version: str

    def __post_init__(self) -> None:
        if not self.version or not self.golden_dataset or not self.evaluator_version:
            raise ValueError("rubric provenance is required")
        if not self.weights or abs(sum(self.weights.values()) - 1) > 1e-9:
            raise ValueError("rubric weights must total one")
        if any(weight <= 0 for weight in self.weights.values()):
            raise ValueError("rubric weights must be positive")


@dataclass(frozen=True)
class CriterionResult:
    criterion: str
    value: float
    confidence: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 1 or not 0 <= self.confidence <= 1:
            raise ValueError("values must be normalized")
        if not self.evidence_refs:
            raise ValueError("criterion evidence is required")


def evaluate_score(rubric: Rubric, results: list[CriterionResult]) -> dict[str, object]:
    by_name = {result.criterion: result for result in results}
    missing = sorted(set(rubric.weights) - set(by_name))
    if missing:
        raise ValueError(f"missing criteria: {', '.join(missing)}")
    score = sum(by_name[name].value * weight for name, weight in rubric.weights.items())
    confidence = sum(
        by_name[name].confidence * weight for name, weight in rubric.weights.items()
    )
    return {
        "rubric_version": rubric.version,
        "evaluator_version": rubric.evaluator_version,
        "golden_dataset": rubric.golden_dataset,
        "score": round(score, 6),
        "confidence": round(confidence, 6),
        "criteria": {
            name: {
                "value": by_name[name].value,
                "confidence": by_name[name].confidence,
                "evidence_refs": list(by_name[name].evidence_refs),
            }
            for name in sorted(rubric.weights)
        },
        "punitive_use_allowed": False,
        "limitation": "This score requires human review and cannot be used alone for punitive decisions.",
    }
