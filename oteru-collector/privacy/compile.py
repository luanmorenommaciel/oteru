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
CONTRACT_VERSION = 2


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
        if action not in ("hash", "mask", "drop"):
            raise PolicyError(
                "identity action must be hash, mask or drop; encrypt is not supported"
            )
    _keys(source["content"], ("known_fields", "log_body"), "content")
    if source["content"]["known_fields"] not in ("drop", "preserve"):
        raise PolicyError("known_fields must be drop or preserve")
    if source["content"]["log_body"] not in ("drop", "preserve"):
        raise PolicyError("log_body must be drop or preserve")
    if source["metrics"] != "preserve_hashed":
        raise PolicyError("metrics must preserve hashed dimensions")


def _present(attributes: str, key: str) -> str:
    # Testing keys, not value != nil, also covers OTLP empty AnyValues.
    return f'ContainsValue(Keys({attributes}), "{key}")'


def _hash_statement(attributes: str, key: str) -> str:
    # contrib v0.155 resource/scope parsers cannot register HMACSHA256, so
    # hash statements run in log/span/datapoint contexts. The IsMatch guard
    # keeps per-record re-entry from hashing resource attributes twice.
    return (
        f'set({attributes}["{key}"], '
        f'Concat(["hmac:v1:", HMACSHA256(Concat(['
        f'"oteru-pii:v1|{key}|", {attributes}["{key}"]], ""))], "")) '
        f"where {_present(attributes, key)} and "
        f'not IsMatch({attributes}["{key}"], "^hmac:v1:[0-9a-f]{{64}}$")'
    )


def _statements(attributes: str, source: dict, *, include_hash: bool = True) -> list[str]:
    result = []
    for key, action in source["identity"].items():
        if action == "hash":
            if include_hash:
                result.append(_hash_statement(attributes, key))
        elif action == "mask":
            result.append(
                f'set({attributes}["{key}"], "REDACTED") where {_present(attributes, key)}'
            )
        else:
            result.append(f'delete_key({attributes}, "{key}")')
    if source["content"]["known_fields"] == "drop":
        result.extend(f'delete_key({attributes}, "{key}")' for key in CONTENT_KEYS)
    return result


def _hash_identity(attributes: str, source: dict) -> list[str]:
    return [
        _hash_statement(attributes, key)
        for key, action in source["identity"].items()
        if action == "hash"
    ]


def _group(context: str, statements: list[str]) -> dict | None:
    if not statements:
        return None
    return {"context": context, "statements": statements}


def _groups(*entries: dict | None) -> list[dict]:
    return [entry for entry in entries if entry is not None]


def _invalid_hash_conditions(contexts: tuple[str, ...], source: dict) -> list[str]:
    result = []
    for context in contexts:
        attributes = f"{context}.attributes"
        for key, action in source["identity"].items():
            if action == "hash":
                result.append(
                    f"{_present(attributes, key)} and "
                    f'(not IsString({attributes}["{key}"]) or '
                    f'{attributes}["{key}"] == "")'
                )
    return result


def compile_policy(source: dict) -> dict:
    validate_policy(source)
    # Fix ordering regardless of JSON key order. No user-supplied OTTL or paths.
    source = {**source, "identity": {k: source["identity"][k] for k in IDENTITY_KEYS}}
    transform = {"error_mode": "propagate"}
    # HMACSHA256 is registered on log/span/spanevent/datapoint factories only.
    # resource/scope contexts keep mask/drop (StandardFuncs); hashes of
    # resource and instrumentation_scope attributes run in the leaf context.
    log_statements = [
        _group("resource", _statements("resource.attributes", source, include_hash=False)),
        _group("scope", _statements("scope.attributes", source, include_hash=False)),
        _group(
            "log",
            (
                _hash_identity("resource.attributes", source)
                + _hash_identity("instrumentation_scope.attributes", source)
                + _statements("log.attributes", source)
            ),
        ),
    ]
    if source["content"]["log_body"] == "drop":
        log_group = next(
            (entry for entry in log_statements if entry and entry["context"] == "log"),
            None,
        )
        if log_group is None:
            log_group = {"context": "log", "statements": []}
            log_statements.append(log_group)
        log_group["statements"].append('set(log.body, "REDACTED")')
    transform["log_statements"] = _groups(*log_statements)
    transform["trace_statements"] = _groups(
        _group("resource", _statements("resource.attributes", source, include_hash=False)),
        _group("scope", _statements("scope.attributes", source, include_hash=False)),
        _group(
            "span",
            (
                _hash_identity("resource.attributes", source)
                + _hash_identity("instrumentation_scope.attributes", source)
                + _statements("span.attributes", source)
            ),
        ),
        _group("spanevent", _statements("spanevent.attributes", source)),
    )
    transform["metric_statements"] = _groups(
        _group(
            "datapoint",
            (
                _hash_identity("resource.attributes", source)
                + _hash_identity("instrumentation_scope.attributes", source)
                + _hash_identity("datapoint.attributes", source)
            ),
        )
    )
    sensitive = [key for key, action in source["identity"].items() if action != "hash"]
    if source["content"]["known_fields"] == "drop":
        sensitive.extend(CONTENT_KEYS)
    return {
        "transform/privacy": transform,
        "filter/privacy_invalid": {
            "error_mode": "propagate",
            "log_conditions": _invalid_hash_conditions(("resource", "scope", "log"), source),
            "trace_conditions": _invalid_hash_conditions(
                ("resource", "scope", "span", "spanevent"), source
            ),
            "metric_conditions": _invalid_hash_conditions(
                ("resource", "scope", "datapoint"), source
            ),
        },
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
