"""Versioned, deterministic risk rules. No collection, transport, or enforcement."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 1
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
MODES = {"default", "plan", "acceptEdits", "auto", "dontAsk", "bypassPermissions"}
KINDS = (
    "bypass_mode_observed",
    "permission_escalation_observed",
    "fixed_threshold_exceeded",
    "declared_component_unapproved",
)
MAX_RECORDS = 10_000
MAX_NS = 2**63 - 1


class InvalidInput(ValueError):
    """Input or policy cannot be evaluated without guessing."""


def _fail(code: str) -> None:
    raise InvalidInput(code)


def _object(value: object, code: str) -> dict:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _list(value: object, code: str) -> list:
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        _fail(code)
    return value


def _int(value: object, code: str, *, minimum: int = 0, maximum: int = MAX_NS) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)
    return value


def _id(value: object, code: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        _fail(code)
    return value


def _digest(value: object, code: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        _fail(code)
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _timestamp(value: int) -> str:
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + (
        f".{nanos:09d}Z"
    )


def _dedupe(records: list, category: str) -> tuple[list[dict], int]:
    unique: dict[str, dict] = {}
    duplicate_count = 0
    for raw in records:
        item = _object(raw, f"{category}_record_invalid")
        identifier = _id(item.get("id"), f"{category}_id_invalid")
        if identifier in unique:
            if unique[identifier] != item:
                _fail(f"{category}_id_conflict")
            duplicate_count += 1
        else:
            unique[identifier] = item
    return list(unique.values()), duplicate_count


def _components(raw: object, category: str) -> list[dict]:
    output: dict[tuple[str, str, str, str], dict] = {}
    by_identity: dict[tuple[str, str], tuple[str, str]] = {}
    for entry in _list(raw, f"{category}_list_invalid"):
        item = _object(entry, f"{category}_entry_invalid")
        kind = item.get("kind")
        if kind not in ("mcp", "plugin", "skill"):
            _fail(f"{category}_kind_invalid")
        identifier = _id(item.get("id"), f"{category}_id_invalid")
        version = _id(item.get("version"), f"{category}_version_invalid")
        digest = _digest(item.get("digest"), f"{category}_digest_invalid")
        identity = (kind, identifier)
        value = (version, digest)
        if category == "inventory" and identity in by_identity and by_identity[identity] != value:
            _fail("inventory_identity_conflict")
        by_identity[identity] = value
        output[(kind, identifier, version, digest)] = {
            "kind": kind,
            "id": identifier,
            "version": version,
            "digest": digest,
        }
    return [output[key] for key in sorted(output)]


def _validate_policy(raw: object) -> dict:
    policy = _object(raw, "policy_invalid")
    if type(policy.get("schema_version")) is not int or policy["schema_version"] != SCHEMA_VERSION:
        _fail("policy_schema_unsupported")
    _id(policy.get("policy_id"), "policy_id_invalid")
    _int(policy.get("policy_version"), "policy_version_invalid", minimum=1)
    severity = _object(policy.get("severity"), "severity_missing")
    required_kinds = list(KINDS[:2])
    if "tool_rate" in policy:
        required_kinds.append("fixed_threshold_exceeded")
    if "allowlist" in policy:
        required_kinds.append("declared_component_unapproved")
    for kind in required_kinds:
        if severity.get(kind) not in ("warning", "high"):
            _fail("severity_invalid")
    if "tool_rate" in policy:
        rate = _object(policy["tool_rate"], "tool_rate_invalid")
        _id(rate.get("baseline_id"), "baseline_id_invalid")
        _int(rate.get("baseline_version"), "baseline_version_invalid", minimum=1)
        _int(rate.get("window_seconds"), "window_invalid", minimum=1, maximum=3600)
        _int(rate.get("threshold"), "threshold_invalid", minimum=0, maximum=MAX_RECORDS)
    if "allowlist" in policy:
        allowed = _object(policy["allowlist"], "allowlist_invalid")
        _id(allowed.get("allowlist_id"), "allowlist_id_invalid")
        _int(allowed.get("allowlist_version"), "allowlist_version_invalid", minimum=1)
        return {
            **policy,
            "allowlist": {**allowed, "entries": _components(allowed.get("entries"), "allowlist")},
        }
    return policy


def _event(
    data: dict,
    policy: dict,
    kind: str,
    rule: str,
    when: int,
    evidence: list[str],
    details: dict,
    session: str | None = None,
) -> dict:
    key = [
        1,
        rule,
        policy["policy_id"],
        policy["policy_version"],
        data["source_id"],
        data["subject_id"],
        session,
        kind,
        sorted(evidence),
        details,
    ]
    result = {
        "schema_version": 1,
        "event_id": hashlib.sha256(_canonical(key).encode("ascii")).hexdigest(),
        "rule_id": rule,
        "rule_version": 1,
        "kind": kind,
        "severity": policy["severity"][kind],
        "observed_at": _timestamp(when),
        "observed_at_ns": when,
        "source_id": data["source_id"],
        "subject_id": data["subject_id"],
        "evidence_ids": sorted(evidence),
        "traffic": data["traffic"],
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "details": details,
    }
    if session is not None:
        result["session_id"] = session
    return result


def evaluate(bundle: object, raw_policy: object) -> tuple[list[dict], dict]:
    """Evaluate a finite, explicit bundle and return findings plus coverage report.

    Unknown fields are discarded at the projection boundary. Neither they nor
    raw records appear in findings or diagnostics.
    """
    data = _object(bundle, "bundle_invalid")
    if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
        _fail("bundle_schema_unsupported")
    _id(data.get("source_id"), "source_id_invalid")
    _id(data.get("subject_id"), "subject_id_invalid")
    if data.get("traffic") not in ("synthetic", "declared"):
        _fail("traffic_invalid")
    policy = _validate_policy(raw_policy)
    coverage = _object(data.get("coverage"), "coverage_missing")
    start = _int(coverage.get("start_ns"), "coverage_start_invalid")
    end = _int(coverage.get("end_ns"), "coverage_end_invalid")
    if start >= end or type(coverage.get("complete")) is not bool:
        _fail("coverage_invalid")

    observations, permission_dups = _dedupe(
        _list(data.get("permission_observations"), "permission_list_invalid"), "permission"
    )
    changes, change_dups = _dedupe(
        _list(data.get("permission_changes", []), "change_list_invalid"), "change"
    )
    if observations and changes:
        _fail("permission_evidence_mixed")
    tool_supplied = "tool_calls" in data
    if tool_supplied and "tool_rate" not in policy:
        _fail("tool_rate_missing")
    tool_calls, tool_dups = _dedupe(_list(data.get("tool_calls", []), "tool_list_invalid"), "tool")
    inventory = data.get("inventory")
    if inventory is not None:
        if "allowlist" not in policy:
            _fail("allowlist_missing")
        inventory = _object(inventory, "inventory_invalid")
        _id(inventory.get("snapshot_id"), "snapshot_id_invalid")
        inventory_time = _int(inventory.get("observed_at_ns"), "inventory_time_invalid")
        components = _components(inventory.get("components"), "inventory")
    else:
        inventory_time = None
        components = []
    diagnostics: set[str] = set()
    findings: list[dict] = []
    change_sequences: set[tuple[str, int]] = set()
    for item in changes:
        session = _id(item.get("session_id"), "change_session_invalid")
        sequence = _int(item.get("sequence"), "change_sequence_invalid", minimum=1)
        if (session, sequence) in change_sequences:
            _fail("change_sequence_conflict")
        change_sequences.add((session, sequence))
        when = _int(item.get("time_unix_nano"), "change_time_invalid")
        before, after = item.get("from_mode"), item.get("to_mode")
        if (
            not isinstance(before, str)
            or len(before) > 32
            or not isinstance(after, str)
            or len(after) > 32
        ):
            _fail("change_mode_invalid")
        if before not in MODES or after not in MODES:
            diagnostics.add("permission_mode_unknown")
        if after == "bypassPermissions":
            escalated = before in MODES and before != "bypassPermissions"
            kind = "permission_escalation_observed" if escalated else "bypass_mode_observed"
            details = {
                "current_mode": after,
                "reason": (
                    "declared_transition"
                    if escalated
                    else "already_bypass"
                    if before == "bypassPermissions"
                    else "unknown_predecessor"
                ),
            }
            if escalated:
                details["previous_mode"] = before
            findings.append(
                _event(
                    data,
                    policy,
                    kind,
                    "claude.permission.bypass",
                    when,
                    [item["id"]],
                    details,
                    session,
                )
            )
    by_session: dict[str, list[dict]] = defaultdict(list)
    for item in observations:
        _id(item.get("session_id"), "permission_session_invalid")
        _int(item.get("sequence"), "permission_sequence_invalid", minimum=1)
        _int(item.get("time_unix_nano"), "permission_time_invalid")
        if not isinstance(item.get("mode"), str) or len(item["mode"]) > 32:
            _fail("permission_mode_invalid")
        by_session[item["session_id"]].append(item)
    for session, entries in by_session.items():
        previous = None
        seen_sequences = set()
        for item in sorted(entries, key=lambda row: row["sequence"]):
            if item["sequence"] in seen_sequences:
                _fail("permission_sequence_conflict")
            seen_sequences.add(item["sequence"])
            current = item["mode"]
            continuous = previous is not None and item["sequence"] == previous["sequence"] + 1
            time_ordered = previous is None or item["time_unix_nano"] >= previous["time_unix_nano"]
            if previous is not None and not continuous:
                diagnostics.add("sequence_gap")
            if not time_ordered:
                diagnostics.add("timestamp_regression")
            if current not in MODES:
                diagnostics.add("permission_mode_unknown")
            elif current == "bypassPermissions":
                escalated = (
                    continuous
                    and time_ordered
                    and previous["mode"] in MODES
                    and previous["mode"] != "bypassPermissions"
                )
                kind = "permission_escalation_observed" if escalated else "bypass_mode_observed"
                reason = (
                    "transition"
                    if escalated
                    else (
                        "initial"
                        if previous is None
                        else "sequence_gap"
                        if not continuous
                        else "timestamp_regression"
                        if not time_ordered
                        else "unknown_predecessor"
                        if previous["mode"] not in MODES
                        else "already_bypass"
                    )
                )
                evidence = [item["id"]]
                details = {"current_mode": current, "reason": reason}
                if escalated:
                    evidence.append(previous["id"])
                    details["previous_mode"] = previous["mode"]
                findings.append(
                    _event(
                        data,
                        policy,
                        kind,
                        "claude.permission.bypass",
                        item["time_unix_nano"],
                        evidence,
                        details,
                        session,
                    )
                )
            previous = item

    rate = policy.get("tool_rate")
    window_ns = rate["window_seconds"] * 1_000_000_000 if rate else 0
    windows: dict[tuple[str, int], set[str]] = defaultdict(set)
    for item in tool_calls:
        session = _id(item.get("session_id"), "tool_session_invalid")
        when = _int(item.get("time_unix_nano"), "tool_time_invalid")
        if not start <= when < end:
            _fail("tool_outside_coverage")
        windows[(session, when // window_ns * window_ns)].add(item["id"])
    tool_evaluable = tool_supplied and coverage["complete"]
    for (session, window_start), ids in sorted(windows.items()):
        window_end = window_start + window_ns
        if window_start < start or window_end > end:
            tool_evaluable = False
            diagnostics.add("partial_window")
            continue
        if not coverage["complete"]:
            continue
        if len(ids) > rate["threshold"]:
            details = {
                "baseline_id": rate["baseline_id"],
                "baseline_version": rate["baseline_version"],
                "baseline_count": rate["threshold"],
                "observed_count": len(ids),
                "difference_count": len(ids) - rate["threshold"],
                "window_start_ns": window_start,
                "window_end_ns": window_end,
                "group_by": "subject_id,session_id",
            }
            findings.append(
                _event(
                    data,
                    policy,
                    "fixed_threshold_exceeded",
                    "tool.calls.fixed_window",
                    window_end,
                    sorted(ids),
                    details,
                    session,
                )
            )
    if not coverage["complete"]:
        diagnostics.add("coverage_unconfirmed")

    inventory_results: list[dict] = []
    if inventory is not None:
        allowed = policy["allowlist"]
        for component in components:
            same_id = [
                entry
                for entry in allowed["entries"]
                if entry["kind"] == component["kind"] and entry["id"] == component["id"]
            ]
            status = (
                "unknown"
                if not same_id
                else "version_mismatch"
                if all(entry["version"] != component["version"] for entry in same_id)
                else "digest_mismatch"
                if all(
                    entry["digest"] != component["digest"]
                    for entry in same_id
                    if entry["version"] == component["version"]
                )
                else "allowed"
            )
            inventory_results.append({**component, "status": status})
            if status == "allowed":
                continue
            details = {
                "component_kind": component["kind"],
                "component_id": component["id"],
                "component_version": component["version"],
                "component_digest": component["digest"],
                "reason": status,
                "allowlist_id": allowed["allowlist_id"],
                "allowlist_version": allowed["allowlist_version"],
            }
            findings.append(
                _event(
                    data,
                    policy,
                    "declared_component_unapproved",
                    "supply_chain.declared_allowlist",
                    inventory_time,
                    [inventory["snapshot_id"]],
                    details,
                )
            )

    findings.sort(key=lambda event: (event["observed_at_ns"], event["event_id"]))
    permission_evaluable = bool(observations or changes) and not diagnostics.intersection(
        {"permission_mode_unknown", "sequence_gap", "timestamp_regression"}
    )
    evaluations = {"permission": "evaluated" if permission_evaluable else "not_evaluable"}
    if tool_supplied or rate is not None:
        evaluations["tool_rate"] = "evaluated" if tool_evaluable else "not_evaluable"
    if inventory is not None or "allowlist" in policy:
        evaluations["inventory"] = "evaluated" if inventory is not None else "not_evaluable"
    report = {
        "schema_version": 1,
        "input_integrity": "valid",
        "declared_coverage": "complete" if coverage["complete"] else "partial",
        "source_observability": "synthetic" if data["traffic"] == "synthetic" else "sampled",
        "source_id": data["source_id"],
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "evaluations": evaluations,
        "diagnostics": sorted(diagnostics),
        "inventory_results": inventory_results,
        "received": len(data["permission_observations"])
        + len(changes)
        + change_dups
        + len(data.get("tool_calls", []))
        + (len(inventory["components"]) if inventory else 0),
        "duplicates": permission_dups
        + change_dups
        + tool_dups
        + ((len(inventory["components"]) - len(components)) if inventory else 0),
        "findings": len(findings),
    }
    return findings, report
