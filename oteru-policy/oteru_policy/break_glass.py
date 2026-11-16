"""Auditable warn, enforce and temporary Break Glass decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Policy:
    version: str
    mode: str = "warn"
    shadow_evidence_ref: str | None = None
    security_review_ref: str | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"warn", "enforce"} or not self.version:
            raise ValueError("invalid policy")
        if self.mode == "enforce" and not all(
            (self.shadow_evidence_ref, self.security_review_ref, self.approved_by)
        ):
            raise ValueError(
                "enforce requires shadow evidence, security review and approval"
            )


@dataclass(frozen=True)
class BreakGlassOverride:
    override_id: str
    subject_scope: str
    provider: str
    reason: str
    incident_ref: str
    approved_by: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("override timestamps must be timezone-aware")
        if not all(
            (
                self.override_id,
                self.subject_scope,
                self.provider,
                self.reason,
                self.incident_ref,
                self.approved_by,
            )
        ):
            raise ValueError("scoped authorization metadata is required")
        if (
            self.expires_at <= self.created_at
            or self.expires_at - self.created_at > timedelta(minutes=60)
        ):
            raise ValueError("override lifetime must be between zero and 60 minutes")

    def is_active(self, now: datetime, subject_scope: str, provider: str) -> bool:
        return (
            self.revoked_at is None
            and self.created_at <= now < self.expires_at
            and self.subject_scope == subject_scope
            and self.provider == provider
        )


def evaluate(
    request_id: str,
    *,
    credential_scope: str | None,
    declared_scope: str | None,
    provider: str,
    policy: Policy,
    evaluated_at: datetime,
    evaluator_available: bool = True,
    is_stale: bool = False,
    override: BreakGlassOverride | None = None,
) -> dict[str, object]:
    """Return a content-free policy decision suitable for an audit trail."""
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    decision_id = hashlib.sha256(
        f"{request_id}:{policy.version}:{evaluated_at.isoformat()}".encode()
    ).hexdigest()
    outcome, reason = "warn", "policy_warn"
    if not credential_scope or (declared_scope and declared_scope != credential_scope):
        outcome, reason = "deny", "invalid_credential_scope"
    elif not evaluator_available:
        outcome, reason, is_stale = "warn", "evaluator_unavailable_fail_open", True
    elif is_stale:
        outcome, reason = "warn", "stale_state_cannot_enforce"
    elif policy.mode == "enforce":
        if override and override.is_active(evaluated_at, credential_scope, provider):
            outcome, reason = "allow_break_glass", "authorized_temporary_override"
        else:
            outcome, reason = "deny", "policy_enforced"
    return {
        "decision_id": decision_id,
        "policy_version": policy.version,
        "mode": policy.mode,
        "outcome": outcome,
        "reason_code": reason,
        "subject_scope": credential_scope,
        "provider": provider,
        "evaluated_at": evaluated_at.isoformat(),
        "is_stale": is_stale,
        "override_id": override.override_id if outcome == "allow_break_glass" else None,
    }
