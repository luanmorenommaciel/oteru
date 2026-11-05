#!/usr/bin/env python3
"""Gate an OTel upgrade from a captured `go list -m -json all` stream."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TARGETS = {
    "go.opentelemetry.io/otel/sdk/log": "v0.21.0",
    "go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc": "v0.21.0",
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace": "v1.45.0",
}


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value)
    if not match:
        raise ValueError(f"unsupported Go module version: {value}")
    return tuple(int(part) for part in match.groups())


def parse_modules(text: str) -> dict[str, str]:
    decoder = json.JSONDecoder()
    position = 0
    modules: dict[str, str] = {}
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text):
            break
        item, position = decoder.raw_decode(text, position)
        if item.get("Path") and item.get("Version"):
            modules[item["Path"]] = item["Version"]
    return modules


def evaluate(modules: dict[str, str]) -> dict[str, dict[str, str | bool]]:
    report = {}
    for module, target in TARGETS.items():
        current = modules.get(module)
        report[module] = {
            "current": current or "missing",
            "target": target,
            "ready": bool(current and version_key(current) >= version_key(target)),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="output of: go list -m -json all")
    args = parser.parse_args()
    report = evaluate(parse_modules(args.input.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all(item["ready"] for item in report.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
