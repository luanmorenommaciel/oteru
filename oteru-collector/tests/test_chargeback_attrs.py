"""#46 chargeback resource attributes: slug allowlist, drop-only, unassigned."""

from __future__ import annotations

import json

import pytest
from support import default, policy

SLUG = "platform-team"
COST = "cost_usd_micros"


def _resource_attrs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"key": key, "value": {"stringValue": value}} for key, value in pairs]


def _log_record(cost: int, extra: list[dict] | None = None) -> dict:
    attrs = [{"key": COST, "value": {"intValue": str(cost)}}]
    if extra:
        attrs.extend(extra)
    return {"body": {"stringValue": "api.request"}, "attributes": attrs}


def _payload(resource_attrs: list[dict], cost: int) -> dict:
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeLogs": [{"logRecords": [_log_record(cost)]}],
            }
        ]
    }


def _sanitize(payload: dict) -> dict:
    clone = json.loads(json.dumps(payload))
    for resource in clone.get("resourceLogs", []):
        attrs = resource.setdefault("resource", {}).setdefault("attributes", [])
        resource["resource"]["attributes"] = policy.drop_invalid_chargeback_attributes(attrs)
    return clone


def _costs(payload: dict) -> tuple[int, int]:
    count = 0
    total = 0
    for resource in payload.get("resourceLogs", []):
        for scope in resource.get("scopeLogs", []):
            for rec in scope.get("logRecords", []):
                count += 1
                for attr in rec.get("attributes", []):
                    if attr.get("key") == COST:
                        total += int(attr["value"]["intValue"])
    return count, total


def _keys(payload: dict) -> set[str]:
    return {
        attr["key"]
        for resource in payload.get("resourceLogs", [])
        for attr in resource.get("resource", {}).get("attributes", [])
    }


def test_valid_slug_passes_intact():
    payload = _payload(_resource_attrs(("oteru.team", SLUG), ("oteru.project", "billing")), 1500)
    out = _sanitize(payload)
    assert _keys(out) == {"oteru.team", "oteru.project"}
    attrs = {
        a["key"]: a["value"]["stringValue"]
        for a in out["resourceLogs"][0]["resource"]["attributes"]
    }
    assert attrs["oteru.team"] == SLUG
    assert attrs["oteru.project"] == "billing"
    assert out["resourceLogs"][0]["scopeLogs"][0]["logRecords"]


def test_at_sign_drops_only_the_attribute():
    payload = _payload(
        _resource_attrs(
            ("oteru.team", "not-an-email@example.com"), ("service.name", "claude-code")
        ),
        2000,
    )
    out = _sanitize(payload)
    assert "oteru.team" not in _keys(out)
    assert "service.name" in _keys(out)
    assert _costs(out) == (1, 2000)


def test_absent_attribute_stays_absent_for_unassigned():
    payload = _payload(_resource_attrs(("service.name", "claude-code")), 900)
    out = _sanitize(payload)
    assert "oteru.team" not in _keys(out)
    assert "oteru.project" not in _keys(out)
    # Downstream COALESCE to the literal unassigned bucket (Modelo dbt).


def test_invalid_team_matches_send_without_the_attribute():
    with_invalid = _payload(
        _resource_attrs(("oteru.team", "Team_Name"), ("service.name", "claude-code")),
        123450,
    )
    without = _payload(_resource_attrs(("service.name", "claude-code")), 123450)
    sanitized_invalid = _sanitize(with_invalid)
    sanitized_without = _sanitize(without)
    assert _costs(sanitized_invalid) == _costs(sanitized_without) == (1, 123450)
    assert _keys(sanitized_invalid) == _keys(sanitized_without)


def test_compile_emits_slug_drop_not_hash_for_chargeback_keys():
    blob = json.dumps(policy.compile_policy(default()))
    assert "oteru.team" in blob
    assert "oteru.project" in blob
    assert "^[a-z0-9][a-z0-9-]{0,62}$" in blob
    assert "HMACSHA256" in blob
    # Chargeback keys are allowlisted, not identity-hashed.
    assert 'HMACSHA256(Concat(["oteru-pii:v1|oteru.team|' not in blob
    source = default()
    source["allowlist"]["oteru.team"] = "hash"
    with pytest.raises(policy.PolicyError):
        policy.compile_policy(source)
