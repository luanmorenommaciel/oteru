"""CLI entrypoint; reads a fixture or collects a GitHub PR, then emits Markdown."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .github import GitHubClient
from .validator import build_report


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--pr", type=int)
    parser.add_argument("--repo", help="owner/name; required with --pr")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.input:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        if not args.repo:
            parser.error("--repo is required with --pr")
        token = os.environ.get(args.token_env)
        if not token:
            parser.error(f"{args.token_env} is not set")
        data = GitHubClient(token).collect(args.repo, args.pr)
    args.output.write_text(build_report(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
