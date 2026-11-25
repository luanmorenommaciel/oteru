#!/usr/bin/env python3
"""Validate the bounded semantic contract of a Compliance Pack manifest."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

STATUSES = {"supported", "partially_supported", "not_supported", "not_applicable"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(pack: dict) -> list[str]:
    errors: list[str] = []
    required = {
        "pack_version",
        "snapshot_id",
        "generated_at",
        "period",
        "scope",
        "requirements",
        "artifacts",
        "limitations",
    }
    missing = sorted(required - pack.keys())
    if missing:
        return ["missing fields: " + ", ".join(missing)]
    if pack["pack_version"] != 1:
        errors.append("pack_version must be 1")
    try:
        if timestamp(pack["period"]["start"]) >= timestamp(pack["period"]["end"]):
            errors.append("period.start must be before period.end")
        timestamp(pack["generated_at"])
    except (KeyError, TypeError, ValueError):
        errors.append("period and generated_at must contain valid ISO-8601 timestamps")

    artifact_paths = set()
    for index, artifact in enumerate(pack["artifacts"]):
        path = str(artifact.get("path", ""))
        if not path or path in artifact_paths:
            errors.append(f"artifacts[{index}].path is missing or duplicate")
        artifact_paths.add(path)
        if not SHA256.fullmatch(str(artifact.get("sha256", ""))):
            errors.append(f"artifacts[{index}].sha256 is invalid")
        if not isinstance(artifact.get("size"), int) or artifact["size"] < 0:
            errors.append(f"artifacts[{index}].size is invalid")

    for index, requirement in enumerate(pack["requirements"]):
        status = requirement.get("status")
        refs = requirement.get("evidence_refs", [])
        limits = requirement.get("limitations", [])
        if status not in STATUSES:
            errors.append(f"requirements[{index}].status is invalid")
        if status in {"supported", "partially_supported"} and not refs:
            errors.append(
                f"requirements[{index}] has a positive status without evidence"
            )
        unknown = sorted(set(refs) - artifact_paths)
        if unknown:
            errors.append(
                f"requirements[{index}] references unknown artifacts: {', '.join(unknown)}"
            )
        if status != "supported" and not limits:
            errors.append(f"requirements[{index}] must state limitations")
    if not pack["limitations"]:
        errors.append("package limitations must not be empty")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    pack = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = validate(pack)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
