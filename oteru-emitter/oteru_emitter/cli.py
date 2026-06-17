"""oteru-emitter CLI.

Phase 1 — ``replay`` subcommand: re-sends an OTLP/JSON capture to the
collector, faithful to the structure, in realtime, over http/protobuf or gRPC.

Examples:
    oteru-emitter replay telemetry.json --dry-run
    oteru-emitter replay telemetry.json --transport http
    oteru-emitter replay telemetry.json --transport grpc --speed 4
    oteru-emitter replay telemetry.json --no-restamp --transport http
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from . import __version__
from .profiles import get_profile, list_profiles
from .rewrite.restamp import restamp
from .scheduler.realtime import run_realtime
from .sources.replay import Batch, load_batches
from .transport.base import DEFAULT_GRPC_ENDPOINT, DEFAULT_HTTP_ENDPOINT


def _event_names(batch: Batch) -> list[str]:
    """Extracts the event names (body.stringValue) from a logs batch."""
    names: list[str] = []
    for rl in batch.payload.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for rec in sl.get("logRecords", []):
                body = rec.get("body", {})
                if isinstance(body, dict) and "stringValue" in body:
                    names.append(body["stringValue"])
    return names


def _summarize(batches: list[Batch]) -> str:
    by_signal = Counter(b.signal for b in batches)
    events: Counter[str] = Counter()
    for b in batches:
        if b.signal == "logs":
            events.update(_event_names(b))
    lines = [
        f"  batches: {len(batches)}  "
        + "  ".join(f"{sig}={n}" for sig, n in sorted(by_signal.items())),
    ]
    if events:
        lines.append("  log events:")
        for name, count in events.most_common():
            lines.append(f"    {count:>4}  {name}")
    return "\n".join(lines)


def _header(value: str) -> tuple[str, str]:
    """argparse type for --header: validates and splits NAME=VALUE."""
    name, sep, val = value.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(
            f"invalid header {value!r}: expected NAME=VALUE, e.g. authorization=<api-key>"
        )
    return name, val


def _build_transport(args):
    headers = dict(args.header)
    if args.transport == "http":
        from .transport.otlp_http import HttpTransport

        endpoint = args.endpoint or DEFAULT_HTTP_ENDPOINT
        return HttpTransport(endpoint, timeout=args.timeout, headers=headers), endpoint
    from .transport.otlp_grpc import GrpcTransport

    endpoint = args.endpoint or DEFAULT_GRPC_ENDPOINT
    return GrpcTransport(endpoint, timeout=args.timeout, headers=headers), endpoint


def cmd_replay(args) -> int:
    profile = get_profile(args.profile)
    try:
        batches = load_batches(args.file)
    except OSError as exc:
        print(f"error: could not read '{args.file}': {exc}", file=sys.stderr)
        return 1
    if args.limit:
        batches = batches[: args.limit]

    if not batches:
        print("no OTLP batches found in the file.", file=sys.stderr)
        return 1

    rotate_keys = () if args.no_restamp else profile.rotate_id_keys
    offset_ns = restamp(
        batches,
        shift_time=not args.no_restamp,
        rotate_keys=rotate_keys,
        seed=args.seed,
    )

    print(f"oteru-emitter {__version__} — replay")
    print(f"  file:      {args.file}")
    print(f"  profile:   {profile.name} ({profile.description})")
    print(
        f"  restamp:   {'off' if args.no_restamp else 'on'}"
        + (
            ""
            if args.no_restamp
            else f"  (offset {offset_ns / 1e9:+.1f}s, rotates IDs={list(rotate_keys)})"
        )
    )
    print(_summarize(batches))

    if args.dry_run:
        print("  [dry-run] nothing was sent.")
        return 0

    transport, endpoint = _build_transport(args)
    print(
        f"  transport: {args.transport} -> {endpoint}"
        f"  (speed={args.speed}x, max-gap={args.max_gap}s)"
    )
    print("  sending... (Ctrl+C to stop)")

    sent = Counter()
    failed = Counter()

    def send(batch: Batch) -> None:
        from .model.otlp import to_request

        try:
            request = to_request(batch.signal, batch.payload)
            transport.send(batch.signal, request)
            sent[batch.signal] += 1
            print(f"    -> {batch.signal} ok ({sum(sent.values())}/{len(batches)})")
        except Exception as exc:  # noqa: BLE001 — report and keep going
            failed[batch.signal] += 1
            print(f"    -> {batch.signal} FAILED: {exc}", file=sys.stderr)

    try:
        run_realtime(
            batches,
            send,
            max_gap=args.max_gap,
            speed=args.speed,
        )
    except KeyboardInterrupt:
        print("\n  interrupted.", file=sys.stderr)
    finally:
        transport.close()

    total_ok = sum(sent.values())
    total_fail = sum(failed.values())
    print(f"  done: {total_ok} sent, {total_fail} failed.")
    return 0 if total_fail == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oteru-emitter",
        description="Forges OTLP traffic faithful to Claude Code (Phase 1: replay).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("replay", help="re-sends an OTLP/JSON capture to the collector")
    r.add_argument("file", help="capture file (one OTLP/JSON batch per line)")
    r.add_argument(
        "--transport",
        choices=["http", "grpc"],
        default="http",
        help="http/protobuf (4318) or gRPC (4317). Default: http",
    )
    r.add_argument(
        "--endpoint",
        default=None,
        help=f"collector endpoint (default: {DEFAULT_HTTP_ENDPOINT} for http, "
        f"{DEFAULT_GRPC_ENDPOINT} for grpc)",
    )
    r.add_argument(
        "--profile",
        choices=list_profiles(),
        default="claude_code",
        help="emitter profile (defines which IDs to rotate). Default: claude_code",
    )
    r.add_argument(
        "--no-restamp",
        action="store_true",
        help="literal replay: does NOT shift timestamps nor rotate IDs",
    )
    r.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="speed multiplier (1.0 = realtime; 4 = 4x faster)",
    )
    r.add_argument(
        "--max-gap",
        type=float,
        default=5.0,
        help="cap (s) for idle gaps between batches. Default: 5",
    )
    r.add_argument(
        "--header",
        action="append",
        type=_header,
        default=[],
        metavar="NAME=VALUE",
        help="extra header sent to the collector (repeatable), "
        "e.g. authorization=<api-key> for ClickStack/HyperDX",
    )
    r.add_argument("--limit", type=int, default=0, help="send only the first N batches")
    r.add_argument("--seed", type=int, default=None, help="seed for ID rotation (reproducible)")
    r.add_argument("--timeout", type=float, default=30.0, help="network timeout (s)")
    r.add_argument("--dry-run", action="store_true", help="validate and summarize only; no send")
    r.set_defaults(func=cmd_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
