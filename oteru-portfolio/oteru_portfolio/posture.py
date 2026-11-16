"""Explainable process posture rules with explicit abstention."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessMetrics:
    coverage: float
    risk: float
    quality: float
    relative_cost: float
    instrumentation: float
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        values = (
            self.coverage,
            self.risk,
            self.quality,
            self.relative_cost,
            self.instrumentation,
        )
        if any(not 0 <= value <= 1 for value in values) or not self.evidence_refs:
            raise ValueError("normalized metrics and evidence are required")


def recommend_posture(metrics: ProcessMetrics) -> dict[str, object]:
    thresholds = {"minimum_coverage": 0.7, "high_risk": 0.7, "low_instrumentation": 0.6}
    if metrics.coverage < thresholds["minimum_coverage"]:
        return {
            "posture": None,
            "reason": "insufficient_coverage",
            "confidence": metrics.coverage,
            "factors": thresholds,
            "evidence_refs": list(metrics.evidence_refs),
            "human_override_allowed": True,
        }
    if metrics.risk >= thresholds["high_risk"]:
        posture, reason = "restrict", "risk_above_threshold"
    elif metrics.instrumentation < thresholds["low_instrumentation"]:
        posture, reason = "instrument", "instrumentation_below_threshold"
    elif metrics.relative_cost >= 0.7 and metrics.quality >= 0.7:
        posture, reason = "optimize", "high_cost_with_acceptable_quality"
    else:
        posture, reason = "scale", "coverage_and_risk_within_thresholds"
    return {
        "posture": posture,
        "reason": reason,
        "confidence": round(min(metrics.coverage, 1 - metrics.risk / 2), 6),
        "factors": thresholds,
        "evidence_refs": list(metrics.evidence_refs),
        "human_override_allowed": True,
    }
