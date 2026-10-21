"""Chargeback resource attributes from OTEL_RESOURCE_ATTRIBUTES (#46).

Only ``oteru.team`` and ``oteru.project`` are copied onto outgoing OTLP
resources. Invalid values are still emitted; the collector drops them.
"""

from __future__ import annotations

import os

CHARGEBACK_KEYS = ("oteru.team", "oteru.project")
_RESOURCE_SECTIONS = ("resourceLogs", "resourceMetrics", "resourceSpans")


def parse_otel_resource_attributes(raw: str) -> dict[str, str]:
    """Parse the W3C/OTel ``key=value,key=value`` resource attribute list."""
    result: dict[str, str] = {}
    if not raw or not raw.strip():
        return result
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def chargeback_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    parsed = parse_otel_resource_attributes(source.get("OTEL_RESOURCE_ATTRIBUTES", ""))
    return {key: parsed[key] for key in CHARGEBACK_KEYS if key in parsed}


def _set_string_attr(resource: dict, key: str, value: str) -> None:
    attrs = resource.setdefault("attributes", [])
    for attr in attrs:
        if attr.get("key") == key:
            attr["value"] = {"stringValue": value}
            return
    attrs.append({"key": key, "value": {"stringValue": value}})


def apply_chargeback_resource_attributes(
    payload: dict, attrs: dict[str, str] | None = None
) -> None:
    """Merge chargeback keys onto every resource in an OTLP/JSON payload."""
    values = chargeback_from_env() if attrs is None else attrs
    if not values:
        return
    for section in _RESOURCE_SECTIONS:
        for entry in payload.get(section, []):
            resource = entry.setdefault("resource", {})
            for key, value in values.items():
                _set_string_attr(resource, key, value)
