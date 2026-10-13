"""Deterministic, side-effect-free policy and review simulation.

Only structured, synthetic requests are accepted. Returned dictionaries are safe
projections: raw parameters and evidence content never cross this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1
POLICY_VERSION = "human-response-v1"
REQUIRED_CHECKS = frozenset({"inventory", "permission", "anomaly"})
REVIEW_WINDOW_SECONDS = 300
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class GateError(ValueError):
    """An input cannot safely be evaluated; message is a stable code."""


def _mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        raise GateError("invalid_object")
    return value


def _safe_id(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise GateError("invalid_identifier")
    return value


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise GateError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError("invalid_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise GateError("invalid_timestamp")
    return parsed.astimezone(timezone.utc)


def _request(value: Any) -> dict:
    request = _mapping(value)
    expected = {
        "schema_version",
        "request_id",
        "action_id",
        "revision",
        "requester_subject",
        "operation",
        "resource_ref",
        "environment",
        "impact_scope",
        "reversible",
        "sensitive_data",
        "privilege_change",
        "parameters",
        "created_at",
    }
    if set(request) != expected or request.get("schema_version") != SCHEMA_VERSION:
        raise GateError("invalid_request_schema")
    for name in ("request_id", "action_id", "requester_subject", "resource_ref"):
        _safe_id(request[name])
    if type(request["revision"]) is not int or request["revision"] < 1:
        raise GateError("invalid_revision")
    if request["operation"] not in ("read", "modify", "delete"):
        raise GateError("invalid_operation")
    if request["environment"] not in ("sandbox", "production"):
        raise GateError("invalid_environment")
    if request["impact_scope"] not in ("limited", "external"):
        raise GateError("invalid_scope")
    for name in ("reversible", "sensitive_data", "privilege_change"):
        if type(request[name]) is not bool:
            raise GateError("invalid_context")
    if not isinstance(request["parameters"], dict):
        raise GateError("invalid_parameters")
    _time(request["created_at"])
    try:
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GateError("invalid_parameters") from exc
    if len(canonical.encode()) > 64 * 1024:
        raise GateError("request_too_large")
    return request


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _checks(value: Any) -> tuple[dict[str, str], dict[str, list[str]]]:
    if not isinstance(value, list) or len(value) > 20:
        raise GateError("invalid_checks")
    statuses: dict[str, str] = {}
    evidence: dict[str, list[str]] = {}
    for raw in value:
        check = _mapping(raw)
        if set(check) != {"check_id", "status", "evidence_refs"}:
            raise GateError("invalid_check_schema")
        name = check["check_id"]
        if name not in REQUIRED_CHECKS or name in statuses:
            raise GateError("invalid_check_id")
        if check["status"] not in ("pass", "fail", "unknown", "error", "expired"):
            raise GateError("invalid_check_status")
        refs = check["evidence_refs"]
        if not isinstance(refs, list) or len(refs) > 20:
            raise GateError("invalid_evidence_refs")
        for ref in refs:
            _safe_id(ref)
        statuses[name] = check["status"]
        evidence[name] = sorted(refs)
    return statuses, evidence


def _snapshot_digest(action_digest: str, statuses: dict, evidence: dict) -> str:
    return _digest(
        {
            "action_digest": action_digest,
            "checks": statuses,
            "evidence": evidence,
            "policy": POLICY_VERSION,
        }
    )


def _consequence(request: dict) -> str:
    if (
        request["operation"] == "delete"
        or request["environment"] == "production"
        or request["impact_scope"] == "external"
        or not request["reversible"]
        or request["sensitive_data"]
        or request["privilege_change"]
    ):
        return "high"
    return "moderate" if request["operation"] == "modify" else "low"


def evaluate(entry: dict, now: datetime) -> dict:
    """Return one initial recommendation for one action and its explicit checks."""
    if now.tzinfo is None:
        raise GateError("invalid_clock")
    item = _mapping(entry)
    request = _request(item.get("request"))
    events = item.get("risk_events", [])
    if not isinstance(events, list) or len(events) > 20:
        raise GateError("invalid_risk_events")
    for event in events:
        event = _mapping(event)
        if set(event) != {"event_id", "action_id", "phase"}:
            raise GateError("invalid_risk_event")
        _safe_id(event["event_id"])
        _safe_id(event["action_id"])
        if event["phase"] == "before":
            raise GateError("unmapped_risk_event")
        if event["phase"] != "after":
            raise GateError("invalid_risk_event")
    statuses, evidence = _checks(item.get("checks"))
    created = _time(request["created_at"])
    if now.astimezone(timezone.utc) < created:
        raise GateError("clock_before_request")
    coverage = len(statuses) == len(REQUIRED_CHECKS) and all(
        status in ("pass", "fail") for status in statuses.values()
    )
    consequence = _consequence(request)
    snapshot_digest = _snapshot_digest(_digest(request), statuses, evidence)
    if statuses.get("inventory") == "fail":
        outcome, reason = "deny", "inventory_denied"
    elif not coverage:
        outcome, reason = "review", "coverage_incomplete"
    elif "fail" in statuses.values():
        outcome, reason = "review", "risk_requires_review"
    elif consequence != "low":
        outcome, reason = "review", "consequence_requires_review"
    else:
        outcome, reason = "allow", "all_checks_passed"
    return {
        "request_id": request["request_id"],
        "action_id": request["action_id"],
        "revision": request["revision"],
        "action_digest": _digest(request),
        "snapshot_digest": snapshot_digest,
        "policy_version": POLICY_VERSION,
        "required_checks": sorted(REQUIRED_CHECKS),
        "coverage_complete": coverage,
        "checks": {
            name: {"status": statuses[name], "evidence_refs": evidence[name]}
            for name in sorted(statuses)
        },
        "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "expires_at": (
            now.astimezone(timezone.utc) + timedelta(seconds=REVIEW_WINDOW_SECONDS)
        ).isoformat(),
        "consequence": consequence,
        "initial_outcome": outcome,
        "reason_code": reason,
        "initial_reason_code": reason,
        "state": {
            "allow": "allowed_simulated",
            "review": "pending_review",
            "deny": "denied_simulated",
        }[outcome],
        "enforcement": "simulated",
        "identity_assurance": "fixture_only",
        "action_execution": "not_controlled",
        "would_execute": False,
    }


def simulate(
    entry: dict,
    now: datetime,
    *,
    review_response: dict | None = None,
    execute_at: str | None = None,
    current_request: dict | None = None,
    current_checks: list | None = None,
    current_policy_version: str | None = None,
) -> dict:
    """Apply a fixture review and fake execution to a recommendation."""
    result = evaluate(entry, now)
    item = _mapping(entry)
    late: list[str] = []
    for event in item.get("risk_events", []):
        event = _mapping(event)
        event_id = _safe_id(event.get("event_id"))
        if event.get("phase") != "before" or event.get("action_id") != result["action_id"]:
            late.append(event_id)
    result["not_authorization_input"] = late
    roster = item.get("roster", [])
    if not isinstance(roster, list) or len(roster) > 20:
        raise GateError("invalid_roster")
    scopes = {}
    for member in roster:
        member = _mapping(member)
        if set(member) != {"reviewer_subject", "operations"}:
            raise GateError("invalid_roster")
        subject = _safe_id(member["reviewer_subject"])
        operations = member["operations"]
        if subject in scopes or not isinstance(operations, list) or not operations:
            raise GateError("invalid_roster")
        if any(operation not in ("read", "modify", "delete") for operation in operations):
            raise GateError("invalid_roster")
        scopes[subject] = operations
    deadline = now.astimezone(timezone.utc) + timedelta(seconds=REVIEW_WINDOW_SECONDS)
    if review_response is not None and result["state"] == "pending_review":
        response = _mapping(review_response)
        responded = _time(response.get("at"))
        if responded < now.astimezone(timezone.utc):
            result["review_error"] = "response_before_evaluation"
        elif responded >= deadline:
            result["state"] = "expired_simulated"
            result["reason_code"] = "review_timeout"
            result["review_error"] = "response_expired"
        else:
            reviewer = response.get("reviewer_subject")
            reason = response.get("reason_code")
            valid_identity = (
                isinstance(reviewer, str)
                and reviewer in scopes
                and item["request"]["operation"] in scopes[reviewer]
                and reviewer != item["request"]["requester_subject"]
            )
            valid_reason = isinstance(reason, str) and bool(SAFE_ID.fullmatch(reason))
            valid_digest = (
                response.get("action_digest") == result["action_digest"]
                and response.get("snapshot_digest") == result["snapshot_digest"]
            )
            if not valid_identity or not valid_reason or not valid_digest:
                result["review_error"] = "invalid_review"
            elif not result["coverage_complete"]:
                result["review_error"] = "coverage_incomplete"
            elif response.get("decision") not in ("approve", "reject"):
                result["review_error"] = "invalid_review"
            else:
                result["state"] = (
                    "approved_simulated"
                    if response["decision"] == "approve"
                    else "rejected_simulated"
                )
                result["reviewer_subject"] = reviewer
                result["reviewed_at"] = responded.isoformat()
                result["review_reason_code"] = reason
    if execute_at is not None:
        attempted = _time(execute_at)
        result["attempted_at"] = attempted.isoformat()
        if attempted < now.astimezone(timezone.utc):
            raise GateError("execution_before_evaluation")
        if result["state"] == "pending_review" and attempted >= deadline:
            result["state"] = "expired_simulated"
            result["reason_code"] = "review_timeout"
        elif result["state"] in ("approved_simulated", "allowed_simulated"):
            if attempted >= deadline:
                result["state"] = "expired_simulated"
                result["reason_code"] = "decision_expired"
            elif (
                current_request is not None
                and _digest(_request(current_request)) != result["action_digest"]
            ):
                result["state"] = "invalidated_simulated"
                result["reason_code"] = "action_changed"
            elif current_policy_version is not None and current_policy_version != POLICY_VERSION:
                result["state"] = "invalidated_simulated"
                result["reason_code"] = "policy_changed"
            elif (
                current_checks is not None
                and _snapshot_digest(result["action_digest"], *_checks(current_checks))
                != result["snapshot_digest"]
            ):
                result["state"] = "invalidated_simulated"
                result["reason_code"] = "checks_changed"
            elif "reviewed_at" in result and _time(result["reviewed_at"]) > attempted:
                result["review_error"] = "execution_before_review"
            else:
                result["would_execute"] = True
    return result
