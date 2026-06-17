# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this directory of the
`oteru` monorepo.

## Overview

`oteru-emitter` forges **OTLP telemetry traffic** so the OTel Collector (the
sibling `oteru-collector/` directory) can be exercised without launching a real Claude
Code session. Phase 1 is **faithful replay**: it reads a captured OTLP/JSON file
and re-sends it, byte-faithful to the original structure, over HTTP or gRPC.

It is a Python package (`oteru_emitter`) with a CLI entry point `oteru-emitter`.

## Commands

```bash
# install (once)
python -m venv .venv && . .venv/Scripts/activate   # bash; PS: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"                            # dev extras: pytest + ruff

# tests + lint (or, from the monorepo root: make test / make lint / make format)
pytest
ruff check . && ruff format --check .

# validate without sending (no collector / network deps needed)
oteru-emitter replay samples/telemetry-sample.json --dry-run

# send to the collector
oteru-emitter replay samples/telemetry-sample.json --transport http
oteru-emitter replay samples/telemetry-sample.json --transport grpc --limit 5 --max-gap 1
```

Without activating the venv: `.\.venv\Scripts\python.exe -m oteru_emitter.cli ...`.

## Architecture

```
OTLP/JSON capture
   │  sources/replay.py      loads batches + anchors timestamps (anchor = min event time)
   ▼
   │  rewrite/restamp.py     shifts time to "now" + rotates IDs (preserves structure)
   ▼
   │  model/otlp.py          dict -> OTLP protobuf message (opentelemetry-proto), neutral model
   ▼
   │  scheduler/realtime.py  paces by the real deltas (--speed, --max-gap)
   ▼
   └► transport/             otlp_http.py (:4318, application/x-protobuf) | otlp_grpc.py (:4317)
```

Key design decisions:

- **Built on `opentelemetry-proto`, NOT the OTel SDK.** The SDK's metric
  aggregation pipeline would recompute data points and lose byte-fidelity. We
  parse the captured OTLP/JSON straight into the proto message
  (`google.protobuf.json_format.ParseDict`) and serialize it for either
  transport — same message, two channels.
- **Restamp separates identity from structure** (`rewrite/restamp.py`). Faithful
  replay ≠ blind replay: timestamps shift to "now" and per-run correlation IDs
  (`session.id`, `prompt.id`, `request_id`) rotate consistently so re-sends don't
  dedupe — but attribute types, ordering, and quirks (the `Str("2501")`
  mistypings, `cost_usd`/`cost_usd_micros` redundancy) are preserved verbatim.
  Principal identity (`user.email`, `organization.id`) is NOT rotated.
- **Profiles are the extension seam** (`profiles/`). `claude_code` declares which
  IDs are per-run correlation vs preserved principal identity. Future profiles
  (Codex, CrewAI) will add synthetic event catalogs and `gen_ai.*` + spans.

## Conventions / gotchas

- **No code changes needed to point at a collector** — endpoints default to
  `localhost:4318` (http) / `localhost:4317` (grpc); override with `--endpoint`.
- **gRPC requires the collector's `grpc:` receiver** — present in the
  `oteru-collector/` config.
- **`samples/telemetry-sample.json` is a committed fixture with PII redacted**
  (real `user.email`/account/org IDs replaced by placeholders). For live data,
  point `replay` at the collector's `telemetry/telemetry.json` output (not
  versioned). Do NOT commit un-redacted captures — they carry user identity.
- **Dry-run needs no dependencies** — parsing/restamp/summary run without
  `opentelemetry-proto`/`grpcio`/`requests`; those load lazily at send time.
- **Tests are Windows-safe and proto-optional.** `tests/` uses `pathlib` only;
  protobuf tests guard with `pytest.importorskip("opentelemetry.proto")` and
  `grpcio` is never required by the suite. The committed fixture
  `tests/fixtures/tiny-capture.json` must stay clean under the PII guard
  (`python ../scripts/check_pii.py`) — placeholder identity values and
  `user@example.com` only.
- **Live Claude Code may be emitting to the same collector.** If
  `CLAUDE_CODE_ENABLE_TELEMETRY=1` is set, the active Claude Code session sends
  real `claude_code.*` telemetry to the same endpoint. Since the emitter replays
  the `Resource` block byte-faithful, synthetic and real traffic are
  indistinguishable at the OTLP envelope; only fragile discriminators differ
  (redacted email `user@example.com` vs real, rotated vs stable `session.id`,
  restamped timestamps). A clean separation (opt-in `--mark-synthetic` resource
  attribute) is **deferred** — see `oteru-collector/CLAUDE.md`.

## Roadmap

- **Phase 1 (current):** faithful replay, dual-transport, realtime, restamp.
- **Phase 2:** stochastic synthetic generator (lifecycle state machine +
  distributions fitted to captures + invariants like `cost = f(tokens, model)`),
  seedable.
- **Phase 3:** new profiles (Codex, CrewAI) incl. `gen_ai.*` + spans.
- **Phase 4:** AI-authored scenario catalog (offline → fixtures).
