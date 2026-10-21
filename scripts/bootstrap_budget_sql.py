"""Split and apply ``bootstrap_budget_roles.sql`` without multi-statement HTTP posts.

ClickHouse HTTP rejects multiple statements (Code 62). This helper splits on
``;`` while ignoring comments, quoted strings, and blank pieces, then posts
one statement per request. Errors print the statement number and the
ClickHouse body after redacting secrets — never a password or a URL that
carries credentials.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

COMMAND_RE = re.compile(
    r"(?i)\b(?:CREATE\s+(?:USER|ROLE|DATABASE)|GRANT|REVOKE|"
    r"DROP\s+(?:USER|ROLE)|ALTER\s+USER)\b"
)
IDENTIFIED_NO_PASSWORD = "IDENTIFIED WITH no_password HOST LOCAL"


def secrets_from_env() -> tuple[str, ...]:
    found = []
    for key in ("OTERU_BUDGET_DEFINER_PASSWORD", "CLICKHOUSE_PASSWORD"):
        value = os.environ.get(key, "")
        if value:
            found.append(value)
    return tuple(found)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Strip known secrets and URL userinfo. Never used for control flow."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return re.sub(r"(https?://)([^/@\s]+@)", r"\1***@", out)


def split_statements(sql: str) -> list[str]:
    """Split on ``;`` outside comments and single-quoted strings."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if not in_single and ch == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                i += 1
            buf.append("\n")
            continue
        if not in_single and ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2 if i < n else 0
            continue
        if ch == "'":
            buf.append(ch)
            if in_single:
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            else:
                in_single = True
            i += 1
            continue
        if ch == ";" and not in_single:
            piece = "".join(buf).strip()
            if piece:
                statements.append(piece)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def command_count(statement: str) -> int:
    return len(COMMAND_RE.findall(statement))


def single_statement_errors(statements: list[str]) -> list[str]:
    """Return human-readable errors if any piece is not one statement."""
    errors: list[str] = []
    if not statements:
        return ["no SQL statements after split"]
    for index, stmt in enumerate(statements, 1):
        count = command_count(stmt)
        pieces = split_statements(stmt)
        if count != 1:
            errors.append(
                f"statement {index} has {count} commands; "
                "multi-statements are not allowed"
            )
        elif len(pieces) != 1:
            errors.append(f"statement {index} is not a single statement")
    return errors


def substitute_definer_password(sql: str, password: str) -> str:
    if not password:
        return sql
    escaped = password.replace("\\", "\\\\").replace("'", "\\'")
    return sql.replace(
        IDENTIFIED_NO_PASSWORD,
        f"IDENTIFIED WITH sha256_password BY '{escaped}' HOST LOCAL",
    )


def clickhouse_url() -> str:
    return os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/")


def clickhouse_user() -> str:
    return os.environ.get("CLICKHOUSE_USER", "default")


def _post(url: str, sql: str, user: str, password: str) -> tuple[int, str]:
    request = urllib.request.Request(url, data=sql.encode("utf-8"), method="POST")
    request.add_header("X-ClickHouse-User", user)
    if password:
        request.add_header("X-ClickHouse-Key", password)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, "request failed"


def probe_http() -> bool:
    code, body = _post(
        clickhouse_url(),
        "SELECT 1\n",
        clickhouse_user(),
        os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )
    return 200 <= code < 300 and body.strip().startswith("1")


def apply_http(sql: str) -> None:
    secrets = secrets_from_env()
    rendered = substitute_definer_password(
        sql, os.environ.get("OTERU_BUDGET_DEFINER_PASSWORD", "")
    )
    statements = split_statements(rendered)
    errors = single_statement_errors(statements)
    if errors:
        print("bootstrap_budget_roles: " + "; ".join(errors), file=sys.stderr)
        raise SystemExit(2)
    user = clickhouse_user()
    password = os.environ.get("CLICKHOUSE_PASSWORD", "")
    url = clickhouse_url()
    for index, stmt in enumerate(statements, 1):
        code, body = _post(url, stmt, user, password)
        if 200 <= code < 300:
            continue
        detail = redact(body.strip() or f"HTTP {code}", secrets)
        print(
            f"bootstrap_budget_roles: statement {index} failed (HTTP {code}): {detail}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def syntax_check(sql: str) -> int:
    statements = split_statements(sql)
    errors = single_statement_errors(statements)
    if errors:
        print("bootstrap_budget_roles: " + "; ".join(errors), file=sys.stderr)
        return 2
    print(f"bootstrap_budget_roles: syntax OK ({len(statements)} statements)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--syntax", metavar="SQL", help="validate split (single statements)"
    )
    group.add_argument(
        "--apply", metavar="SQL", help="apply via HTTP, one statement per request"
    )
    group.add_argument(
        "--probe", action="store_true", help="exit 0 if HTTP SELECT 1 succeeds"
    )
    args = parser.parse_args(argv)
    if args.probe:
        return 0 if probe_http() else 1
    path = args.syntax or args.apply
    sql = Path(path).read_text(encoding="utf-8")
    if args.syntax:
        return syntax_check(sql)
    apply_http(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
