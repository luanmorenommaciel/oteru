# Fixed tool-call baseline (#64)

This stacked PR adds a deterministic tool-call rule to the shared risk
contract introduced by #63. Review its diff against `operational-risk-63`.
Collection remains caller supplied; there is no adaptive learning, continuous
monitor, or enforcement.

The bundle may include `tool_calls`, each with a unique `id`, `session_id`, and
`time_unix_nano`. Its existing `subject_id` and `coverage` provide the other
evaluation dimensions. If calls are supplied, policy must include `tool_rate`
with `baseline_id`, `baseline_version`, `window_seconds` (1–3600), and
`threshold` (0–10000). Missing or invalid baseline metadata exits 2. An
absent call list with configured policy is `not_evaluable`; an explicit empty
list with complete coverage evaluates to zero findings.

The rule counts unique call IDs by subject, session, and UTC epoch-aligned
`[start,end)` window. It evaluates only windows wholly covered by the
producer's completeness claim. Above the threshold, it emits
`fixed_threshold_exceeded` with baseline ID/version/count, observed count,
difference, window bounds, and grouping dimension. This is a fixed limit, not
a learned anomaly or an inference of agent intent.

## Definition of done

```bash
make test-risk             # 9/10/11 boundaries, grouping and failure cases pass
make demo-risk-tool-rate   # 10 calls normal; 11 calls -> 1 finding
make lint-risk             # #64 code, tests, and E2E script pass Ruff
make pii-guard             # generated output is scanned
make dry-run               # existing sample still parses
make e2e-risk-tool-rate    # Docker: 1 logical finding, 2 rows after replay
```

The demo's 10 calls / 60 seconds and severity are synthetic policy. Production
owners must set the measured population, calibrated threshold, false-positive
budget, policy version owner, and alert route. Rollback is to stop invoking the
opt-in evaluator/replay and revert this branch. The #63 evaluator remains
usable without a `tool_rate` policy block.
