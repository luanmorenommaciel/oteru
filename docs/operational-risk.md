# Operational risk observation (#63, #64, #66)

This is a finite, offline evaluator. It reads explicitly named JSON files, emits
versioned findings, and can replay their projected OTLP logs through the existing
emitter. It neither installs hooks nor changes agent permissions. The default
policy and four finding types are demonstrated with synthetic input; no production
threshold, alert route, or enforcement decision is implied.

## Boundaries and decisions

| Boundary | Responsibility |
|---|---|
| Collection | The caller supplies a bundle or OTLP capture path. There is no host inventory scan. |
| Transformation | `source_claude.py` extracts only mode-change fields from an explicit Claude Code capture. |
| Policy | The caller supplies a versioned limit, allowlist, and severity map. No missing policy defaults to approval. |
| Detection | `rules.py` evaluates three deterministic rules over projected data. |
| Transport | `output.py` writes JSONL and an OTLP logs batch; existing replay sends the batch. |
| Enforcement | None. A finding cannot grant, deny, revoke, or alter a permission. |

The redacted committed capture has **10** `claude_code.permission_mode_changed`
records under `com.anthropic.claude_code.events` (Claude Code 2.1.169–2.1.170).
They carry `session.id`, `event.sequence`, `from_mode`, `to_mode`, and a log
timestamp. None enters `bypassPermissions`. This validates field extraction for
those historical records; it does **not** validate detection of a real bypass
transition on a current Claude version. `event.sequence` can have gaps because
other event types occupy sequence values. For direct mode-change records, the
transition is declared by that one record; no adjacency inference is needed.

The minimal slice lives inside `oteru_emitter.risk`, where the existing Python
test and CI tooling already run. No collector config, replay behavior, database
schema, or default endpoint changes. Unknown schema versions fail closed.

## Input and output contract v1

`python -m oteru_emitter.risk BUNDLE POLICY NEW_OUTPUT_DIR` accepts UTF-8 JSON
files up to 2 MB each. The directory must not exist. It publishes three files
together: `findings.jsonl`, `otlp.jsonl`, and `report.json`. It ignores unknown
input fields at the projection boundary and never echoes raw input values in
errors. IDs have a restricted character set and length; a SHA-256 digest is
required for each declared component. The maximum is 10,000 records per list.

The bundle contains:

| Field | Meaning |
|---|---|
| `schema_version` | `1` |
| `source_id`, `subject_id` | Opaque IDs supplied by the caller |
| `traffic` | `synthetic` or `declared`; declared means caller-supplied, not verified live traffic |
| `coverage` | `start_ns`, `end_ns`, `complete`; the last field is a claim by the producer |
| `permission_observations` | `id`, `session_id`, `sequence`, `time_unix_nano`, `mode` |
| `permission_changes` | Optional alternative: `id`, `session_id`, `sequence`, `time_unix_nano`, `from_mode`, `to_mode` |
| `tool_calls` | `id`, `session_id`, `time_unix_nano` for each normalized call start |
| `inventory` | Optional declared `snapshot_id`, `observed_at_ns`, `components` |

Use observations or changes in a bundle, never both. The historical Claude
adapter produces changes. A single initial observation in bypass yields
`bypass_mode_observed`; consecutive observations from a known other mode yield
`permission_escalation_observed`. A direct change from a known other mode to
bypass also yields the latter. Gaps, unknown modes, and regressing timestamps
cannot fabricate a transition and mark the observation evaluation `not_evaluable`.
A request for permission or an `accept` decision
alone is not a mode change.

The policy contains `schema_version: 1`, `policy_id`, `policy_version`, a
`severity` mapping for all four finding kinds, `tool_rate` with `baseline_id`,
`baseline_version`, `window_seconds`, and `threshold`, and `allowlist` with
`allowlist_id`, `allowlist_version`, and `entries`. Each allowlist entry and
inventory component has `kind` (`mcp`, `plugin`, or `skill`), `id`, `version`,
and lowercase SHA-256 `digest`. Matching is exact. The digest is a **declared
value**; this evaluator does not hash an installed component or certify a host.
Missing or conflicting metadata fails. An explicit empty allowlist approves
zero components. An absent inventory is `not_evaluable`; an explicit empty
inventory evaluates zero declared components.

The fixed tool-rate rule counts unique call IDs per subject, session and
60-second UTC window, with `[start,end)` boundaries. The synthetic example
uses `count > 10`. It reports `fixed_threshold_exceeded`, meaning only that
the configured limit was exceeded. It is not a learned behavioral anomaly.
Incomplete declared coverage or a partial window is `not_evaluable`; zero
findings does not certify normal operation.

Each finding has a stable `event_id` derived from the rule and policy versions,
source, subject, session when present, finding kind, evidence IDs, and typed
details. Reprocessing identical inputs preserves IDs and canonical JSON bytes.
The `oteru.risk` OTLP scope and `oteru.risk.detected` log body distinguish
findings from native Claude events. ClickHouse can receive duplicate physical
rows on retry; count distinct `oteru.risk.event_id` for logical findings.

`report.json` separates `input_integrity`, `declared_coverage`, and
`source_observability`, then reports each evaluation as `evaluated` or
`not_evaluable`. `synthetic` and `sampled` describe evidence origin, not
universal coverage. Exit 0 means all three evaluations ran, even with findings;
exit 2 means invalid input or publication failure; exit 3 means one or more
evaluations could not run. Exit 3 still publishes the report and any supported
findings. A structurally invalid input publishes no output directory.

## Run and verify

```bash
make setup
make test-risk
make lint-risk
make demo-risk-offline
make test
make lint
make pii-guard
make dry-run
```

The offline demo expects **four distinct findings**, one per kind, and uses no
network or Docker. The tests cover 36 mode pairs (5 transitions and 1 repeated
bypass state on the second observation), thresholds 9/10/11 (0/0/1 finding),
12 allowlist combinations (3 approved, 9 findings), deterministic reprocessing,
PII canaries, malformed data, and an OTLP model round trip. `make dry-run`
continues to report 523 sample batches. The standard CI workflow already
includes the emitter package and tests on its configured platform matrix.

To project a caller-supplied, sanitized Claude Code file:

```bash
cd oteru-emitter
.venv/bin/python -m oteru_emitter.risk.source_claude \
  samples/telemetry-sample.json /tmp/oteru-permissions.json \
  --source-id example-capture --subject-id example-subject
```

Use a separate explicit policy file with the evaluator. The projected sample
has no inventory or complete tool-call coverage, so a combined evaluation
correctly returns exit 3 with those two areas `not_evaluable`. New captures may
contain identity; do not commit them or forward raw captures to external services.

`make e2e-risk` requires Docker. It creates a uniquely named Compose project,
uses dynamically assigned loopback ports and a temporary telemetry mount,
sends the four synthetic findings twice via the existing HTTP replay, and
queries ClickHouse for **8 physical rows and 4 distinct event IDs**. Its own
project and volume are removed afterward. It does not stop or remove another
stack. A failed ingest or query returns nonzero; HTTP success alone is
insufficient evidence of persistence.

## Open decisions and rollback

- Real Claude bypass telemetry on a current supported version is unverified.
  A sanitized example supplied explicitly is required before claiming live
  compatibility or installing a hook adapter.
- `10 calls / 60 seconds` and the severity map are demonstration values.
  Production owners must choose the measured population, limit, false-positive
  budget, policy versioning owner, and alert route.
- An inventory producer and its completeness guarantee are not defined. This
  slice verifies only supplied declarations, not installed MCP servers,
  plugins, or skills.
- There is no continuous monitor, deduplicated notification service, managed
  policy distribution, or enforcement. These remain outside the three-issue
  slice delivered here.

Rollback consists of stopping the opt-in runner/replay and reverting this
branch. Existing emitter and collector defaults do not change. Findings already
persisted remain identified by their scope and versions; rollback does not erase
them. Preserve `event_id` semantics within v1, and introduce a new rule or
policy version when behavior changes.
