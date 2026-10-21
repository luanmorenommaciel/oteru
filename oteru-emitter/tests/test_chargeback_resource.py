"""Emitter copies oteru.team/oteru.project from OTEL_RESOURCE_ATTRIBUTES."""

from __future__ import annotations

from oteru_emitter import resource_attrs as ra
from oteru_emitter.cli import main
from oteru_emitter.resource_attrs import (
    apply_chargeback_resource_attributes,
    chargeback_from_env,
    parse_otel_resource_attributes,
)


def test_parse_ignores_unrelated_keys():
    parsed = parse_otel_resource_attributes(
        "service.name=claude-code,oteru.team=platform,oteru.project=billing,host.name=box"
    )
    assert parsed["oteru.team"] == "platform"
    assert parsed["oteru.project"] == "billing"
    assert chargeback_from_env(
        {"OTEL_RESOURCE_ATTRIBUTES": "service.name=claude-code,oteru.team=platform"}
    ) == {"oteru.team": "platform"}


def test_absent_env_emits_no_chargeback_keys():
    payload = {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": []}]}
    apply_chargeback_resource_attributes(payload, attrs={})
    keys = [a["key"] for a in payload["resourceLogs"][0]["resource"]["attributes"]]
    assert "oteru.team" not in keys
    assert "oteru.project" not in keys


def test_invalid_value_is_still_emitted_for_collector_to_drop():
    payload = {"resourceLogs": [{"resource": {"attributes": []}, "scopeLogs": []}]}
    apply_chargeback_resource_attributes(payload, attrs={"oteru.team": "not-an-email@example.com"})
    attrs = {
        a["key"]: a["value"]["stringValue"]
        for a in payload["resourceLogs"][0]["resource"]["attributes"]
    }
    assert attrs["oteru.team"] == "not-an-email@example.com"


def test_replay_applies_env_chargeback_attrs(tiny_path, monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "oteru.team=platform,oteru.project=billing")
    seen: list[dict] = []

    def capture(payload, attrs=None):
        ra.apply_chargeback_resource_attributes(payload, attrs)
        seen.append(payload)

    monkeypatch.setattr("oteru_emitter.cli.apply_chargeback_resource_attributes", capture)
    assert main(["replay", str(tiny_path), "--dry-run", "--emit", "log"]) == 0
    assert seen
    keys = {
        a["key"]: a["value"]["stringValue"]
        for a in seen[0]["resourceLogs"][0]["resource"]["attributes"]
    }
    assert keys["oteru.team"] == "platform"
    assert keys["oteru.project"] == "billing"
