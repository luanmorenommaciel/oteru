#!/usr/bin/env python3
"""Audits ``event.sequence`` per session: gaps and duplicates.

Claude Code stamps every log record with ``event.sequence``, a counter that
starts at zero. It is monotonic **per run, not per session**: a resumed
session reuses its ``session.id`` and resets the counter. Counting repeated
``(session.id, event.sequence)`` pairs without segmenting runs first is
therefore meaningless -- on the committed sample it reports 95 duplicates,
all of them false.

So this script segments runs first, then looks for gaps and duplicates
inside each run.

What it can prove: a record missing from the middle of a run, and a run
whose head was lost. What it cannot see: a truncated tail, a run or session
lost whole, and anything at all about metrics or spans -- they do not carry
``event.sequence``. See the caveats printed with every report.

Stdlib-only, following ``check_pii.py``.

Usage::

    scripts/check_sequence_gaps.py                  # local ClickHouse
    scripts/check_sequence_gaps.py --source sample  # committed capture
    scripts/check_sequence_gaps.py --json

Exit codes: 0 clean, 1 anomalies found, 2 could not read the source.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "oteru-emitter" / "samples" / "telemetry-sample.json"

# Same default as scripts/check_signals_e2e.sh.
CLICKHOUSE_URL = os.environ.get(
    "CLICKHOUSE_URL", "http://localhost:8123/?user=otel&password=otel"
)

QUERY = """
SELECT
    LogAttributes['session.id']      AS session_id,
    LogAttributes['event.sequence']  AS sequence,
    LogAttributes['event.name']      AS event_name,
    toString(Timestamp)              AS ts,
    Body                             AS body
FROM otel.otel_logs
WHERE session_id != '' AND sequence != ''
ORDER BY session_id, Timestamp, toUInt64OrNull(sequence)
FORMAT JSONEachRow
"""


class SourceError(Exception):
    """The capture or the database could not be read."""


# --------------------------------------------------------------------------
# Sources. Both yield the same record shape:
#   (session_id, sequence:int, event_name, timestamp, fingerprint)
# The fingerprint stands in for record identity: it is what separates a
# duplicate that is a re-delivery of the same record from one that is two
# different records sharing a number.
# --------------------------------------------------------------------------


def from_clickhouse(url: str) -> list[tuple]:
    # CLICKHOUSE_URL carries credentials as query parameters by repo
    # convention. Move them into an Authorization header: it keeps them out
    # of redirects and server logs. They must not travel both ways at once
    # -- ClickHouse rejects that outright (error 516).
    parts = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parts.query)
    user = params.pop("user", [None])[0]
    password = params.pop("password", [""])[0]
    endpoint = urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(params, doseq=True))
    )

    request = urllib.request.Request(endpoint, data=QUERY.encode("utf-8"))
    if user:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:200]
        raise SourceError(f"ClickHouse rejected the query ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SourceError(f"ClickHouse unreachable at {endpoint}: {exc}") from exc

    records = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sequence = _to_int(row["sequence"])
        if sequence is None:
            continue
        records.append(
            (
                row["session_id"],
                sequence,
                row["event_name"],
                row["ts"],
                f"{row['ts']}|{row['event_name']}|{row['body']}",
            )
        )
    return records


def from_sample(path: Path) -> list[tuple]:
    if not path.is_file():
        raise SourceError(f"capture not found: {path}")

    records = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                batch = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceError(f"{path}:{lineno}: invalid JSON: {exc}") from exc

            for resource in batch.get("resourceLogs", []):
                for scope in resource.get("scopeLogs", []):
                    for record in scope.get("logRecords", []):
                        attributes = _attributes(record.get("attributes", []))
                        session = attributes.get("session.id")
                        sequence = _to_int(attributes.get("event.sequence"))
                        if not session or sequence is None:
                            continue
                        timestamp = record.get("timeUnixNano", "0")
                        records.append(
                            (
                                session,
                                sequence,
                                attributes.get("event.name", ""),
                                timestamp,
                                json.dumps(record, sort_keys=True),
                            )
                        )

    records.sort(key=lambda record: (record[0], int(record[3]), record[1]))
    return records


def _attributes(raw: list) -> dict:
    """Flattens an OTLP attribute list into a plain dict."""
    flat = {}
    for item in raw:
        value = item.get("value", {})
        flat[item["key"]] = next(iter(value.values()), "") if value else ""
    return flat


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Run segmentation -- the part that makes the numbers mean anything.
# --------------------------------------------------------------------------


def collapse_redeliveries(records: list[tuple]) -> tuple[list[tuple], dict]:
    """Pulls re-delivered records out before runs are segmented.

    Two records sharing a session, a sequence *and* a byte-identical payload
    are one record delivered twice. That has to be settled first, because
    segmentation cannot see it: a re-delivery of record 0 looks exactly like
    a counter restart, and would silently become a run boundary.

    Divergent records sharing a sequence are left alone. At sequence 0 they
    are what a restart looks like -- all 53 such pairs in the committed
    sample were restarts, not duplicates -- so segmentation should get them.
    """
    seen: dict[tuple, tuple] = {}
    redeliveries: dict[tuple, int] = {}
    kept = []

    for record in records:
        # Keyed on the payload too, not just session and sequence: a session
        # with several runs has several records numbered 0, and a key of
        # (session, sequence) alone would report one re-delivery against
        # every one of them.
        key = (record[0], record[1], record[4])
        if key in seen:
            redeliveries[key] = redeliveries.get(key, 1) + 1
            continue
        seen[key] = record
        kept.append(record)

    return kept, redeliveries


def segment(records: list[tuple]) -> dict[str, list[list[tuple]]]:
    """Splits each session's records into runs.

    Records arrive ordered by ``(session, timestamp, sequence)``. Ordering by
    timestamp alone is not enough: timestamps tie (two records inside the
    same millisecond are common), and an arbitrarily ordered tie would look
    like the counter went backwards.

    A run boundary is a counter restart. Three cases, deliberately kept
    apart:

    * ``sequence == 0`` -- a clean restart. Every run observed so far begins
      at zero, with a startup event.
    * ``sequence < previous`` but not zero -- also a restart, but this run's
      own zero never arrived, so its head was lost.
    * ``sequence == previous`` -- *not* a boundary. Two distinct records
      share a number; cutting here would hide that.

    Byte-identical re-deliveries must already have been removed by
    :func:`collapse_redeliveries`; this function cannot tell one from a
    restart.
    """
    runs: dict[str, list[list[tuple]]] = {}

    for record in records:
        session, sequence = record[0], record[1]
        session_runs = runs.setdefault(session, [])

        if not session_runs:
            session_runs.append([record])
            continue

        current = session_runs[-1]
        previous = current[-1][1]
        if sequence == 0 or sequence < previous:
            session_runs.append([record])
        else:
            current.append(record)

    return runs


def analyse(
    runs: dict[str, list[list[tuple]]], redeliveries: dict[tuple, int] | None = None
) -> list[dict]:
    """Builds the per-run report."""
    redeliveries = redeliveries or {}
    report = []

    for session in sorted(runs):
        for index, run in enumerate(runs[session]):
            sequences = [record[1] for record in run]
            low, high = min(sequences), max(sequences)
            present = set(sequences)

            missing = sorted(set(range(low, high + 1)) - present)

            # Two kinds, and they mean opposite things. A re-delivery is the
            # same record arriving twice -- delivery working too hard, no
            # data lost. A divergent duplicate is two different records
            # claiming one number inside a single run, which the counter is
            # not supposed to allow.
            duplicates = []
            for value in sorted(present):
                same = [record for record in run if record[1] == value]
                for record in same:
                    copies = redeliveries.get((session, value, record[4]))
                    if copies:
                        duplicates.append(
                            {"sequence": value, "copies": copies, "kind": "re-delivery"}
                        )
                if len(same) > 1:
                    duplicates.append(
                        {"sequence": value, "copies": len(same), "kind": "divergent"}
                    )

            report.append(
                {
                    "session": session,
                    "run": index + 1,
                    "runs_in_session": len(runs[session]),
                    "records": len(run),
                    "first_sequence": low,
                    "last_sequence": high,
                    "head_lost": low != 0,
                    "missing": missing,
                    "duplicates": duplicates,
                    "first_event": run[0][2],
                    "last_event": run[-1][2],
                }
            )

    return report


def has_anomaly(entry: dict) -> bool:
    return bool(entry["missing"] or entry["duplicates"] or entry["head_lost"])


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

CAVEATS = """
What this audit cannot see:
  * a truncated tail -- no event marks the end of a run, so a dropped tail
    only makes the last sequence smaller. This is the likeliest loss mode
    (a process exiting before the exporter flushes), and it is invisible
    here. "No gaps" is not "no loss".
  * a run or a session lost whole -- nothing is left to inspect.
  * metrics and spans -- they do not carry event.sequence at all.
  * where a record was lost -- the audit sees the arrival end only.

The 'last event' column is the one handle on tails: a run ending on a
startup-phase event is worth a second look."""


def render(report: list[dict], total: int) -> str:
    lines = []
    anomalies = [entry for entry in report if has_anomaly(entry)]

    lines.append(
        f"{total} log records with event.sequence; "
        f"{len({entry['session'] for entry in report})} sessions; "
        f"{len(report)} runs"
    )
    resumed = sorted(
        {entry["session"] for entry in report if entry["runs_in_session"] > 1}
    )
    if resumed:
        lines.append(
            f"{len(resumed)} session(s) resumed (counter restarted): "
            + ", ".join(session[:8] for session in resumed)
        )
    lines.append("")

    header = f"{'session':10} {'run':>5} {'records':>8} {'range':>12} {'gaps':>6} {'dups':>6}  last event"
    lines.append(header)
    lines.append("-" * len(header))

    for entry in report:
        span = f"{entry['first_sequence']}..{entry['last_sequence']}"
        flag = " <- head lost" if entry["head_lost"] else ""
        lines.append(
            f"{entry['session'][:8]:10} "
            f"{entry['run']:>5} "
            f"{entry['records']:>8} "
            f"{span:>12} "
            f"{len(entry['missing']):>6} "
            f"{len(entry['duplicates']):>6}  "
            f"{entry['last_event']}{flag}"
        )

    if anomalies:
        lines.append("")
        lines.append("Detail:")
        for entry in anomalies:
            label = f"  {entry['session'][:8]} run {entry['run']}:"
            if entry["head_lost"]:
                lines.append(
                    f"{label} head lost -- run starts at {entry['first_sequence']}, not 0"
                )
            if entry["missing"]:
                shown = ", ".join(str(value) for value in entry["missing"][:20])
                more = (
                    f" (+{len(entry['missing']) - 20} more)"
                    if len(entry["missing"]) > 20
                    else ""
                )
                lines.append(f"{label} missing sequence(s) {shown}{more}")
            for duplicate in entry["duplicates"]:
                note = (
                    "identical payload -- one record delivered twice, nothing lost"
                    if duplicate["kind"] == "re-delivery"
                    else "distinct records sharing one number inside a run"
                )
                lines.append(
                    f"{label} sequence {duplicate['sequence']} appears "
                    f"{duplicate['copies']}x, {duplicate['kind']} ({note})"
                )

    lines.append("")
    if anomalies:
        missing = sum(len(entry["missing"]) for entry in report)
        duplicated = sum(len(entry["duplicates"]) for entry in report)
        rate = f" ({missing / total:.3%} of records)" if total else ""
        lines.append(
            f"FOUND: {missing} missing record(s){rate}, "
            f"{duplicated} duplicated sequence(s), "
            f"{sum(1 for entry in report if entry['head_lost'])} run(s) missing their head"
        )
    else:
        lines.append("No gaps, no duplicates, every run starts at 0.")

    lines.append(CAVEATS)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--source",
        choices=("clickhouse", "sample"),
        default="clickhouse",
        help="where to read log records from (default: clickhouse)",
    )
    parser.add_argument(
        "--url",
        default=CLICKHOUSE_URL,
        help="ClickHouse HTTP endpoint (env: CLICKHOUSE_URL)",
    )
    parser.add_argument(
        "--capture",
        type=Path,
        default=SAMPLE,
        help=f"OTLP/JSON capture to read with --source sample (default: {SAMPLE})",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead")
    args = parser.parse_args()

    try:
        if args.source == "sample":
            records = from_sample(args.capture)
        else:
            records = from_clickhouse(args.url)
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not records:
        print("no log records carrying event.sequence -- nothing to audit")
        return 0

    deduped, redeliveries = collapse_redeliveries(records)
    report = analyse(segment(deduped), redeliveries)

    if args.json:
        print(
            json.dumps(
                {
                    "source": args.source,
                    "records": len(records),
                    "runs": report,
                    "anomalies": sum(1 for entry in report if has_anomaly(entry)),
                },
                indent=2,
            )
        )
    else:
        print(render(report, len(records)))

    return 1 if any(has_anomaly(entry) for entry in report) else 0


if __name__ == "__main__":
    sys.exit(main())
