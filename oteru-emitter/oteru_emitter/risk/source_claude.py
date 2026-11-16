"""Project explicit Claude Code OTLP log captures into permission-change evidence.

The adapter reads only the path passed by the caller. It does not inspect
transcripts, settings, the user's home directory, or tool content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .output import canonical
from .rules import MAX_NS, InvalidInput, _id, _int, _object

MAX_CAPTURE_BYTES = 32_000_000
MAX_LINE_BYTES = 2_000_000
EVENT_NAME = "claude_code.permission_mode_changed"
SCOPE = "com.anthropic.claude_code.events"


def _attributes(raw: object) -> dict:
    if not isinstance(raw, list):
        raise InvalidInput("attributes_invalid")
    result = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise InvalidInput("attribute_invalid")
        key = item["key"]
        if key in ("session.id", "event.sequence", "from_mode", "to_mode"):
            if key in result or not isinstance(item.get("value"), dict):
                raise InvalidInput("permission_attribute_invalid")
            result[key] = item["value"]
    return result


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("duplicate_json_key")
        result[key] = value
    return result


def _objects(raw: object, code: str) -> list[dict]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise InvalidInput(code)
    return raw


def _field(attributes: dict, key: str, value_key: str) -> object:
    value = attributes.get(key, {})
    if not isinstance(value, dict):
        raise InvalidInput("permission_attribute_invalid")
    return value.get(value_key)


def _sequence(value: object) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise InvalidInput("sequence_invalid")
    return _int(int(value), "sequence_invalid", minimum=1)


def project_capture(capture: Path, source_id: str, subject_id: str) -> dict:
    """Extract only explicit mode-change records; never infer a missing event."""
    _id(source_id, "source_id_invalid")
    _id(subject_id, "subject_id_invalid")
    if capture.stat().st_size > MAX_CAPTURE_BYTES:
        raise InvalidInput("capture_too_large")
    changes = []
    with capture.open("rb") as handle:
        for line in handle:
            if len(line) > MAX_LINE_BYTES:
                raise InvalidInput("capture_line_too_large")
            try:
                payload = json.loads(line, object_pairs_hook=_unique_keys)
            except (ValueError, UnicodeError) as exc:
                raise InvalidInput("capture_json_invalid") from exc
            if not isinstance(payload, dict):
                raise InvalidInput("capture_record_invalid")
            for resource in _objects(payload.get("resourceLogs", []), "resource_logs_invalid"):
                for scope in _objects(resource.get("scopeLogs", []), "scope_logs_invalid"):
                    scope_info = _object(scope.get("scope"), "scope_invalid")
                    if scope_info.get("name") != SCOPE:
                        continue
                    for record in _objects(scope.get("logRecords", []), "log_records_invalid"):
                        body = _object(record.get("body"), "log_body_invalid")
                        if body.get("stringValue") != EVENT_NAME:
                            continue
                        attrs = _attributes(record.get("attributes"))
                        session = _id(_field(attrs, "session.id", "stringValue"), "session_invalid")
                        sequence = _sequence(_field(attrs, "event.sequence", "intValue"))
                        before = _id(_field(attrs, "from_mode", "stringValue"), "from_mode_invalid")
                        after = _id(_field(attrs, "to_mode", "stringValue"), "to_mode_invalid")
                        timestamp = record.get("timeUnixNano")
                        if not isinstance(timestamp, str) or not timestamp.isdecimal():
                            raise InvalidInput("time_invalid")
                        when = _int(int(timestamp), "time_invalid")
                        identity = [source_id, session, sequence, when, before, after]
                        digest = hashlib.sha256(canonical(identity).encode("ascii")).hexdigest()
                        changes.append(
                            {
                                "id": digest,
                                "session_id": session,
                                "sequence": sequence,
                                "time_unix_nano": when,
                                "from_mode": before,
                                "to_mode": after,
                            }
                        )
    return {
        "schema_version": 1,
        "source_id": source_id,
        "subject_id": subject_id,
        "traffic": "declared",
        # No claim that the file contains every event emitted by the session.
        "coverage": {"start_ns": 0, "end_ns": MAX_NS, "complete": False},
        "permission_observations": [],
        "permission_changes": changes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project explicit Claude Code permission events")
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--subject-id", required=True)
    args = parser.parse_args(argv)
    try:
        bundle = project_capture(args.capture, args.source_id, args.subject_id)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(canonical(bundle) + "\n")
    except InvalidInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: operation_failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"projected={len(bundle['permission_changes'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
