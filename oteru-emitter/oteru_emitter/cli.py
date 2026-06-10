"""CLI do oteru-emitter.

Fase 1 — subcomando ``replay``: reenvia uma captura OTLP/JSON ao collector,
fiel à estrutura, em tempo real, via http/protobuf ou gRPC.

Exemplos:
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
    """Extrai os nomes de evento (body.stringValue) de uma batch de logs."""
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
        lines.append("  eventos de log:")
        for name, count in events.most_common():
            lines.append(f"    {count:>4}  {name}")
    return "\n".join(lines)


def _build_transport(args):
    if args.transport == "http":
        from .transport.otlp_http import HttpTransport

        endpoint = args.endpoint or DEFAULT_HTTP_ENDPOINT
        return HttpTransport(endpoint, timeout=args.timeout), endpoint
    from .transport.otlp_grpc import GrpcTransport

    endpoint = args.endpoint or DEFAULT_GRPC_ENDPOINT
    return GrpcTransport(endpoint, timeout=args.timeout), endpoint


def cmd_replay(args) -> int:
    profile = get_profile(args.profile)
    try:
        batches = load_batches(args.file)
    except OSError as exc:
        print(f"erro: não foi possível ler '{args.file}': {exc}", file=sys.stderr)
        return 1
    if args.limit:
        batches = batches[: args.limit]

    if not batches:
        print("nenhuma batch OTLP encontrada no arquivo.", file=sys.stderr)
        return 1

    rotate_keys = () if args.no_restamp else profile.rotate_id_keys
    offset_ns = restamp(
        batches,
        shift_time=not args.no_restamp,
        rotate_keys=rotate_keys,
        seed=args.seed,
    )

    print(f"oteru-emitter {__version__} — replay")
    print(f"  arquivo:   {args.file}")
    print(f"  profile:   {profile.name} ({profile.description})")
    print(
        f"  restamp:   {'off' if args.no_restamp else 'on'}"
        + (
            ""
            if args.no_restamp
            else f"  (offset {offset_ns / 1e9:+.1f}s, rota IDs={list(rotate_keys)})"
        )
    )
    print(_summarize(batches))

    if args.dry_run:
        print("  [dry-run] nada foi enviado.")
        return 0

    transport, endpoint = _build_transport(args)
    print(
        f"  transporte: {args.transport} -> {endpoint}"
        f"  (speed={args.speed}x, max-gap={args.max_gap}s)"
    )
    print("  enviando... (Ctrl+C para parar)")

    sent = Counter()
    failed = Counter()

    def send(batch: Batch) -> None:
        from .model.otlp import to_request

        try:
            request = to_request(batch.signal, batch.payload)
            transport.send(batch.signal, request)
            sent[batch.signal] += 1
            print(f"    -> {batch.signal} ok ({sum(sent.values())}/{len(batches)})")
        except Exception as exc:  # noqa: BLE001 — reportar e seguir
            failed[batch.signal] += 1
            print(f"    -> {batch.signal} FALHOU: {exc}", file=sys.stderr)

    try:
        run_realtime(
            batches,
            send,
            max_gap=args.max_gap,
            speed=args.speed,
        )
    except KeyboardInterrupt:
        print("\n  interrompido.", file=sys.stderr)
    finally:
        transport.close()

    total_ok = sum(sent.values())
    total_fail = sum(failed.values())
    print(f"  concluído: {total_ok} enviadas, {total_fail} falhas.")
    return 0 if total_fail == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oteru-emitter",
        description="Forja tráfego OTLP fiel ao Claude Code (Fase 1: replay).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("replay", help="reenvia uma captura OTLP/JSON ao collector")
    r.add_argument("file", help="arquivo de captura (uma batch OTLP/JSON por linha)")
    r.add_argument(
        "--transport",
        choices=["http", "grpc"],
        default="http",
        help="http/protobuf (4318) ou gRPC (4317). Padrão: http",
    )
    r.add_argument(
        "--endpoint",
        default=None,
        help=f"endpoint do collector (padrão: {DEFAULT_HTTP_ENDPOINT} p/ http, "
        f"{DEFAULT_GRPC_ENDPOINT} p/ grpc)",
    )
    r.add_argument(
        "--profile",
        choices=list_profiles(),
        default="claude_code",
        help="profile do emissor (define quais IDs rotacionar). Padrão: claude_code",
    )
    r.add_argument(
        "--no-restamp",
        action="store_true",
        help="replay literal: NÃO desloca timestamps nem rotaciona IDs",
    )
    r.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="multiplicador de velocidade (1.0 = tempo real; 4 = 4x mais rápido)",
    )
    r.add_argument(
        "--max-gap",
        type=float,
        default=5.0,
        help="teto (s) para gaps ociosos entre batches. Padrão: 5",
    )
    r.add_argument("--limit", type=int, default=0, help="envia só as N primeiras batches")
    r.add_argument("--seed", type=int, default=None, help="seed para rotação de IDs (reproduzível)")
    r.add_argument("--timeout", type=float, default=30.0, help="timeout de rede (s)")
    r.add_argument("--dry-run", action="store_true", help="só valida e resume; não envia")
    r.set_defaults(func=cmd_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
