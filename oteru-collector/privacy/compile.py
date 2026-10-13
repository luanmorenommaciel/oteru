#!/usr/bin/env python3
"""Compile the bounded privacy contract to native Collector processors (stdlib only).

No telemetry is handled here. Only fixed catalog keys can become OTTL; customer
policy values never become executable expressions or diagnostic content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

IDENTITY_KEYS = (
    "user.email",
    "user.id",
    "user.account_uuid",
    "user.account_id",
    "organization.id",
    "host.name",
)
CONTENT_KEYS = (
    "user_prompt",
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
)
CONTRACT_VERSION = 1


class PolicyError(ValueError):
    """An invalid policy; messages must not echo user-controlled values."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("duplicate policy key")
        result[key] = value
    return result


def load_policy(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError("policy is missing or is not valid UTF-8 JSON") from exc


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PolicyError(f"{label} requires exactly the documented keys")


def validate_policy(source: dict) -> None:
    _keys(source, ("version", "identity", "content", "metrics"), "policy")
    if type(source["version"]) is not int or source["version"] != CONTRACT_VERSION:
        raise PolicyError("unsupported policy version")
    _keys(source["identity"], IDENTITY_KEYS, "identity")
    for action in source["identity"].values():
        if action not in ("mask", "drop"):
            raise PolicyError("identity action must be mask or drop; encrypt is not supported")
    _keys(source["content"], ("known_fields", "log_body"), "content")
    if source["content"]["known_fields"] not in ("drop", "preserve"):
        raise PolicyError("known_fields must be drop or preserve")
    if source["content"]["log_body"] not in ("drop", "preserve"):
        raise PolicyError("log_body must be drop or preserve")
    if source["metrics"] != "reject_sensitive":
        raise PolicyError("metrics must reject sensitive dimensions")


def _present(attributes: str, key: str) -> str:
    # Testing keys, not value != nil, also covers OTLP empty AnyValues.
    return f'ContainsValue(Keys({attributes}), "{key}")'


def _statements(attributes: str, source: dict) -> list[str]:
    result = []
    for key, action in source["identity"].items():
        if action == "mask":
            result.append(
                f'set({attributes}["{key}"], "REDACTED") where {_present(attributes, key)}'
            )
        else:
            result.append(f'delete_key({attributes}, "{key}")')
    if source["content"]["known_fields"] == "drop":
        result.extend(f'delete_key({attributes}, "{key}")' for key in CONTENT_KEYS)
    return result


def compile_policy(source: dict) -> dict:
    validate_policy(source)
    # Fix ordering regardless of JSON key order. No user-supplied OTTL or paths.
    source = {**source, "identity": {k: source["identity"][k] for k in IDENTITY_KEYS}}
    transform = {"error_mode": "propagate"}
    for signal, contexts in (
        ("log", ("resource", "scope", "log")),
        ("trace", ("resource", "scope", "span", "spanevent")),
    ):
        transform[f"{signal}_statements"] = [
            {"context": context, "statements": _statements(f"{context}.attributes", source)}
            for context in contexts
        ]
    if source["content"]["log_body"] == "drop":
        transform["log_statements"][-1]["statements"].append('set(log.body, "REDACTED")')
    sensitive = list(IDENTITY_KEYS)
    if source["content"]["known_fields"] == "drop":
        sensitive.extend(CONTENT_KEYS)
    return {
        "transform/privacy": transform,
        # Link attributes cannot be transformed in this Collector release.
        # Reject the containing span before any exporter sees the link.
        "filter/privacy_links": {
            "error_mode": "propagate",
            "trace_conditions": ["Len(span.links) > 0"],
        },
        "filter/privacy_metrics": {
            "error_mode": "propagate",
            "metric_conditions": [
                {
                    "context": context,
                    "conditions": [_present(f"{context}.attributes", key) for key in sensitive],
                }
                for context in ("resource", "scope", "datapoint")
            ]
            + [{"context": "datapoint", "conditions": ["Len(datapoint.exemplars) > 0"]}],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    temporary = None
    try:
        # Fail closed even when a previous compilation exists in the volume.
        args.output.unlink(missing_ok=True)
        source = load_policy(args.policy)
        compiled = compile_policy(source)
        canonical = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        checksum = hashlib.sha256(canonical).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(compiled, stream, indent=2)
            stream.write("\n")
        temporary.chmod(0o644)
        os.replace(temporary, args.output)
        print(f"privacy: contract={CONTRACT_VERSION} sha256={checksum}")
        return 0
    except (PolicyError, OSError) as exc:
        message = str(exc) if isinstance(exc, PolicyError) else "cannot publish policy"
        print(f"privacy: {message}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
