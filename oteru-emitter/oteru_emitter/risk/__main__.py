"""Run the offline evaluator on two explicit JSON inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .output import write_output
from .rules import InvalidInput, evaluate

MAX_INPUT_BYTES = 2_000_000


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("duplicate_json_key")
        result[key] = value
    return result


def _read(path: Path) -> dict:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidInput("input_too_large")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidInput("json_invalid") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate explicit operational-risk evidence offline"
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("output_dir", type=Path, help="new directory; never overwritten")
    args = parser.parse_args(argv)
    try:
        findings, report = evaluate(_read(args.bundle), _read(args.policy))
        write_output(args.output_dir, findings, report)
    except InvalidInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: operation_failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"findings={len(findings)} output={args.output_dir}")
    return 0 if all(value == "evaluated" for value in report["evaluations"].values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
