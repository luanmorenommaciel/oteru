"""Refuse to touch any ClickHouse that is not an explicit throwaway instance.

Call require() (or post()) before any ClickHouse read or write in tests and
check scripts. Allowed only when OTERU_EPHEMERAL_CH=1 and either
CLICKHOUSE_HOST is localhost/127.0.0.1/::1, or CLICKHOUSE_HOST is the
'clickhouse' service name with GITHUB_ACTIONS=true. CLICKHOUSE_PORT must be
set explicitly (digits). Outside CI the default local port 8123 is refused,
so a developer's long-lived ClickHouse is never the target; run the
throwaway instance on another port (e.g. 18123).

Before any query, post() also calls require_marker(): the target must
already hold the table default.oteru_ephemeral_marker (created only by the
throwaway setup), checked over HTTP with credentials sent only in the
X-ClickHouse-User / X-ClickHouse-Key headers. A missing marker or any HTTP
or connection error refuses. Nothing secret goes in the URL, argv or logs. Connection values come only from CLICKHOUSE_HOST,
CLICKHOUSE_PORT, CLICKHOUSE_USER and CLICKHOUSE_PASSWORD; credentials are
never printed.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
REFUSE_EXIT = 2
# Default port of a developer's long-lived local ClickHouse. Refused outside CI.
LOCAL_DEFAULT_PORT = "8123"
MARKER_TABLE = "default.oteru_ephemeral_marker"
HTTP_TIMEOUT_S = 10


def check(env: Mapping[str, str] | None = None) -> str | None:
    """Return a refusal reason, or None when the target is allowed."""
    env = os.environ if env is None else env
    if env.get("OTERU_EPHEMERAL_CH") != "1":
        return "OTERU_EPHEMERAL_CH=1 is required"
    host = env.get("CLICKHOUSE_HOST", "")
    if not host:
        return "CLICKHOUSE_HOST is required"
    in_ci = env.get("GITHUB_ACTIONS") == "true"
    if host not in LOCAL_HOSTS and not (host == "clickhouse" and in_ci):
        return f"CLICKHOUSE_HOST {host!r} is not an allowed ephemeral host"
    port = env.get("CLICKHOUSE_PORT", "")
    if not port.isdigit():
        return "CLICKHOUSE_PORT must be set explicitly"
    if port == LOCAL_DEFAULT_PORT and not in_ci:
        return "CLICKHOUSE_PORT 8123 is the long-lived local ClickHouse; use an ephemeral port"
    return None


def require(env: Mapping[str, str] | None = None) -> None:
    reason = check(env)
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        raise SystemExit(REFUSE_EXIT)


def url(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    host = env["CLICKHOUSE_HOST"]
    host = f"[{host}]" if ":" in host else host
    return f"http://{host}:{env['CLICKHOUSE_PORT']}/"


def _send(sql: str, env: Mapping[str, str]) -> str:
    """POST sql to the guarded target; credentials only in headers."""
    req = urllib.request.Request(url(env), data=sql.encode(), method="POST")
    req.add_header("X-ClickHouse-User", env.get("CLICKHOUSE_USER", "default"))
    req.add_header("X-ClickHouse-Key", env.get("CLICKHOUSE_PASSWORD", ""))
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return resp.read().decode()


def marker_reason(env: Mapping[str, str] | None = None) -> str | None:
    """Return a refusal reason unless MARKER_TABLE exists on the target."""
    env = os.environ if env is None else env
    try:
        out = _send(f"EXISTS TABLE {MARKER_TABLE}", env)
    except urllib.error.HTTPError as exc:
        # Status code only: the body or URL could echo request details.
        return f"marker check failed with HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any failure must refuse
        return f"marker check failed ({type(exc).__name__})"
    if out.strip() != "1":
        return f"{MARKER_TABLE} is missing on the target"
    return None


def require_marker(env: Mapping[str, str] | None = None) -> None:
    """Refuse (exit 2) unless the host rules pass and the marker table exists."""
    require(env)
    reason = marker_reason(env)
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        raise SystemExit(REFUSE_EXIT)


def post(sql: str, env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    require_marker(env)
    return _send(sql, env)
