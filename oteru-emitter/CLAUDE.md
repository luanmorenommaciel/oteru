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
   │                         + select_signals() backs --emit log,metric,trace
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
- **Two kinds of ID, two rotation paths.** OTLP carries identity in two
  distinct places, and restamp has to cover both. *Attributes*
  (`session.id`, `prompt.id`, `request_id`) live inside `{"key":…,"value":…}`
  entries and are profile-specific — `profiles/` declares which ones rotate.
  *Structural fields* (`traceId`, `spanId`, `parentSpanId` on spans, log
  records and links) are plain keys on the OTLP object, universal to the
  protocol, and no profile governs them. An attribute-walking rotation cannot
  reach them: that is why every replay used to re-send the same `traceId`,
  producing one backend trace holding spans from several runs with conflicting
  `session.id`s. `_rotate_structural_ids` keys its mapping by *byte length*,
  not field name, so a `parentSpanId` maps to the same new value as the
  `spanId` it points at and the span tree survives — and one mapping is shared
  across every batch of the run, which is what keeps a log record's `traceId`
  pointing at the same trace as its span.
- **Restamp separates identity from structure** (`rewrite/restamp.py`). Faithful
  replay ≠ blind replay: timestamps shift to "now" and per-run correlation IDs
  (`session.id`, `prompt.id`, `request_id`) rotate consistently so re-sends don't
  dedupe — but attribute types, ordering, and quirks (the `Str("2501")`
  mistypings, `cost_usd`/`cost_usd_micros` redundancy) are preserved verbatim.
  Principal identity (`user.email`, `organization.id`) is NOT rotated.
- **Profiles are the extension seam** (`profiles/`). `claude_code` declares which
  IDs are per-run correlation vs preserved principal identity. Future profiles
  (Codex, CrewAI) will add synthetic event catalogs and `gen_ai.*` + spans.
- **`--emit` selects, it does not generate.** It filters the loaded batches by
  signal (`sources/replay.py::select_signals`), so it can only narrow what the
  capture already holds; asking for an absent signal exits 1 rather than sending
  nothing. Synthetic *generation* of signals is Phase 2 — do not grow `--emit`
  into a generator. The three signals are independent by design: no combination
  is rejected and none implies another.

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
- **Trace/span IDs are hex in OTLP/JSON, base64 in protobuf JSON.** The
  collector's `file` exporter writes `"traceId":"5b8aa5a2…"` (lowercase hex),
  but `ParseDict` maps `bytes` fields from base64 — feeding it the hex straight
  would make the collector answer `400 invalid TraceID length`. `model/otlp.py`
  re-encodes `traceId`/`spanId`/`parentSpanId` before parsing
  (`normalize_ids`), leaving empty strings and wrong-length values untouched.
  This only ever bites on traces, which is why it went unnoticed: Claude Code
  emits none.
- **Spans anchor on `startTimeUnixNano`, other signals do not.** Spans carry no
  `timeUnixNano`, so without that exception a traces-only capture would have no
  anchor and restamp would silently leave stale timestamps
  (`sources/replay.py::anchor_keys_for`). For metrics the key stays excluded —
  there it marks the cumulative-series start and would distort the pacing.
  `endTimeUnixNano` is in `SHIFT_TIME_KEYS` for the same reason: shifting only a
  span's start corrupts its duration.
- **Tests are Windows-safe and proto-optional.** `tests/` uses `pathlib` only;
  protobuf tests guard with `pytest.importorskip("opentelemetry.proto")` and
  `grpcio` is never required by the suite. The committed fixture
  `tests/fixtures/tiny-capture.json` must stay clean under the PII guard
  (`python ../scripts/check_pii.py`) — placeholder identity values and
  `user@example.com` only.
- **Trace captures are built, not committed** (`tests/factories.py`). The repo
  ships code to run locally, never captured telemetry — no emitted or collected
  data is versioned. `conftest.py` writes the built payload to `tmp_path`, and
  `scripts/check_signals_e2e.sh` builds it into a temp file the same way. The
  schema (span names, attribute keys, hierarchy) was read off a real capture on
  2026-07-29 under Claude Code 2.1.191 and 2.1.220, so it is observed fact; the
  values are fabricated and identity is placeholder-only. When the schema
  changes, re-read it from a live capture and update the factory — do not commit
  the capture.
- **Scope names drift across versions — bump them together with the version.**
  The trace scope is `com.anthropic.claude_code.traces` up to 2.1.170 and
  `com.anthropic.claude_code.tracing` from 2.1.191 on. The factory got this
  wrong exactly once, and instructively: it grew out of a 2.1.170 capture where
  `.traces` was correct, the `service.version` strings were bumped to 2.1.220,
  and the scope name rode along unreviewed. Nothing failed — the payload is
  well-formed OTLP either way, so it took querying real traffic to notice. A
  rename like this makes a `WHERE ScopeName = …` return nothing without erroring;
  `profiles/base.py::expected_scopes` lists both spellings for that reason.
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
