# Operational risk v1: permission observation (#63)

The #63 base branch starts the shared, opt-in operational-risk contract and
delivers permission evidence to a versioned finding, report, OTLP log, and
queryable ClickHouse row. This stacked #64 branch adds a fixed, declared
tool-call baseline. The evaluator is finite and
offline. It does not install hooks, monitor continuously, or change permissions.

## Scope and boundaries

The caller supplies a named OTLP capture or a projected JSON bundle. The Claude
adapter extracts only `session.id`, `event.sequence`, `from_mode`, `to_mode`, and
timestamp from `claude_code.permission_mode_changed` records. It never reads
transcripts, settings, tool inputs, or host configuration. Policy supplies the
severity for both finding kinds. Detection and output have no enforcement path.

The committed redacted sample from Claude Code 2.1.169–2.1.170 contains ten real
mode-change records and **no** transition into `bypassPermissions`. It verifies
field extraction and the no-escalation case. The positive bypass fixture is
synthetic and uses the same OTLP record shape. A current-version real bypass
capture is still needed before claiming live positive-case compatibility.

## Contract v1

Run `python -m oteru_emitter.risk BUNDLE POLICY NEW_OUTPUT_DIR` from
`oteru-emitter`. Inputs are UTF-8 JSON, at most 2 MB each. The output directory
must not exist. A valid run atomically publishes `findings.jsonl`, `otlp.jsonl`,
and `report.json`; invalid input publishes nothing. Unknown schema versions,
conflicting IDs, missing severities, and duplicate JSON keys fail visibly.

The bundle contains `schema_version: 1`, opaque `source_id` and `subject_id`,
`traffic` (`synthetic` or `declared`), `coverage` (`start_ns`, `end_ns`,
`complete`), and `permission_observations`. It may contain
`permission_changes` instead of observations. A change has `id`, `session_id`,
`sequence`, `time_unix_nano`, `from_mode`, and `to_mode`. The adapter returns
direct changes. The policy contains `schema_version: 1`, `policy_id`,
`policy_version`, and `severity` entries for `bypass_mode_observed` and
`permission_escalation_observed` (`warning` or `high`). The example uses `high`.

A known change from another mode into bypass yields
`permission_escalation_observed`. An initial bypass state, repeated bypass, or
unknown predecessor yields `bypass_mode_observed`; the evaluator never invents
a transition. Gaps, unknown modes, and timestamp regressions make observation
coverage `not_evaluable`. A direct `permission_mode_changed` record declares
its own transition, so unrelated sequence gaps do not invalidate it. A request
for permission or an `accept` decision alone is not a mode change.

Every finding carries rule and policy versions, severity, evidence IDs, and a
stable `event_id` derived from validated, projected fields. Reprocessing the
same input produces the same ID and JSON bytes. Retry can produce duplicate
physical ClickHouse rows; query distinct `oteru.risk.event_id` for logical
alerts. `oteru.risk.detected` is the OTLP log body and `oteru.risk` its scope.
`report.json` reports input integrity, declared coverage, source observability,
and permission evaluation status. Exit 0 means evaluation ran, including when
it found risk; exit 2 means invalid input or output failure; exit 3 means the
permission check could not be evaluated. Exit 3 still publishes a report.

## Demo and definition of done

```bash
make test-risk             # permission contract and capture-to-output tests pass
make lint-risk             # Ruff check and format check pass
make demo-risk-permission  # 1 synthetic escalation; ordinary change has no finding
make test                  # full emitter suite passes
make lint                  # full lint and formatting pass
make pii-guard             # captures and generated payloads contain no PII leak
make dry-run               # existing 523-batch sample still parses
make e2e-risk              # Docker: 2 logical findings, 4 rows after replay
```

The offline demo exercises capture → adapter → detector → finding → output.
The E2E test sends that output through the existing emitter and an isolated
collector to ClickHouse, then queries the persisted finding by OTLP scope,
kind, policy version, and distinct event ID. It uses a unique Compose project
and removes only its own volume. No collector defaults or database schema change.

To project an explicitly supplied sanitized capture:

```bash
cd oteru-emitter
.venv/bin/python -m oteru_emitter.risk.source_claude \
  samples/telemetry-sample.json /tmp/oteru-permissions.json \
  --source-id example-capture --subject-id example-subject
.venv/bin/python -m oteru_emitter.risk \
  /tmp/oteru-permissions.json policy.json /tmp/oteru-risk-result
```

The caller must provide `policy.json`. New captures may contain identity; do
not commit or forward raw captures. Rollback is to stop invoking this opt-in
runner/replay and revert the branch. Previously stored findings retain their
scope and IDs. #66 adds declared inventory comparison in a stacked,
separately reviewable branch.

## Fixed tool-call baseline (#64)

This branch adds an optional `tool_calls` list to the bundle and an optional
`tool_rate` block to the policy. Each call contains a unique `id`, `session_id`,
and `time_unix_nano`; the surrounding bundle already supplies `subject_id` and
coverage. When calls are supplied, `tool_rate` is required and invalid or
missing baseline metadata fails with exit 2. An absent call list is
`not_evaluable` if a baseline was configured; an explicit empty list with
complete coverage evaluates to zero findings. This preserves the #63 input
contract for callers that do not use #64.

The baseline declares `baseline_id`, `baseline_version`, `window_seconds`
(1–3600), and `threshold` (0–10000). Unique call IDs are counted by subject,
session, and UTC epoch-aligned `[start,end)` window. A window is evaluated only
when the producer declares complete coverage for the entire window. A count
strictly above the threshold emits `fixed_threshold_exceeded`. The finding
includes baseline ID/version/count, observed count, difference, window bounds,
and `group_by: subject_id,session_id`. It is a fixed threshold, not a learned
anomaly or claim about behavioral intent. The producer's completeness claim
is not independently verified.

```bash
make test-risk             # 9, 10, 11-call boundaries and failure cases pass
make demo-risk-tool-rate   # 10 calls normal; 11 calls -> one finding
make lint-risk             # detector, demo, tests and E2E script pass Ruff
make pii-guard             # generated normal/anomalous output is scanned
make dry-run               # existing capture still parses
make e2e-risk              # Docker: 2 logical findings, 4 rows after replay
```

The synthetic demonstration threshold and severity require owner calibration
before operational use: choose the population, expected rate, false-positive
budget, policy version owner, and alert route. No adaptive learning or
continuous monitor is implemented.
