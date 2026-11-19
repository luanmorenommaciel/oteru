# Human gate simulation — issue #65

The simulator evaluates **synthetic actions** against three explicit check
results (`inventory`, `permission`, `anomaly`). It records `allow`, `review`, or
`deny` and can simulate a human response. `would_execute` is a boolean in the
report; the simulator has no executor, network client, or credential. All
simulator decisions say `enforcement=simulated`. The optional Claude Code
adapter below can block or request confirmation for calls inside that runtime,
but it does not make the simulator's synthetic decisions operational.

## Run

From the repository root after `make setup`:

```bash
make test-human-gate
make lint-human-gate
make demo-human-gate GATE_OUTPUT=tmp/human-gate-demo
```

`GATE_OUTPUT` must name a **new** directory. The command writes `audit.jsonl` and
`report.json` there, then prints a summary. The committed V1 demo checks 13
valid scenarios: `allow=1`, `review=11`, `deny=1`, `would_execute=2`. It also
requires an invalid check fixture to fail with `invalid_check_status` before
creating output. `tmp/` is ignored by Git.
Run the demo again with a different output path; existing runs are never overwritten.
The demo uses no Docker, ClickHouse, collector, IdP or network connection.

You can invoke another explicit, redacted scenario with:

```bash
python -m oteru_gate.cli path/to/scenario.json tmp/another-new-run
```

The root [proposal](../docs/specs/issue-65-human-response-proposal.md) describes
contracts, policy precedence, limitations and acceptance cases. The CLI accepts
one run with 1–20 cases. Every case contains a `request`, explicit `checks`,
`now`, and optional `review_response`, `execute_at`, `current_request`,
`current_checks` and `current_policy_version`.
The `entry` holds `request`, `checks`, and optional `roster` and `risk_events`.
See [`fixtures/demo.json`](fixtures/demo.json) for the full scenario matrix and
[`fixtures/invalid-check.json`](fixtures/invalid-check.json) for the expected
rejection. The checks are **fixture inputs**, not results fetched from #63/#64/#66.
An event with `phase=after` is reported as `not_authorization_input`; a raw
`phase=before` event without a mapped check fails explicitly.

A response to `review` must carry `reviewer_subject`, `decision` (`approve` or
`reject`), `reason_code`, `at`, `action_digest` and `snapshot_digest`. A valid
approver must be in the case's roster, scoped to the operation, and different
from the requester. Read the two digests from an initial evaluation/report;
they bind the response to one action and one set of checks. The roster is a
fixture, **not authentication**.

## Defaults and failure behavior

- Policy `human-response-v1`: explicit inventory failure → `deny`; incomplete
  coverage → `review` that cannot be approved; other failed check or moderate/high
  consequence → `review`; complete, low consequence with all checks passing →
  `allow`.
- Consequence is high for deletion, production, external reach,
  irreversibility, sensitive data or privilege change; moderate for remaining
  modifications; low for sandbox reads. These rules are synthetic defaults.
- Review and decision validity end **300 seconds** after evaluation. At the
  exact boundary, expiration wins. Silence never becomes approval.
- Exit 0: run completed, including review/deny outcomes. Exit 2: invalid input
  or policy fixture. Exit 1: I/O failure. Error output uses stable codes and
  never prints raw input. The entire run is validated before output creation.
- Report and audit contain only structured identifiers, check status, safe
  evidence references, digests, decisions and timestamps. They never copy raw
  action parameters. Files are created with private permissions; failed audit
  writes do not yield a success report. This is local evidence, not tamper-proof
  storage. POSIX files are created with mode `0600`; Windows uses the inherited
  ACL. The repository PII guard does not scan generated run output; a
  dedicated canary test checks the output projection.

The V1 matrix shows low-risk allow, pending review, approved and rejected
reviews, explicit deny, expiry, parameter/evidence/policy changes, absent and
expired checks, execution attempted without approval, and a post-action
finding. Only the low-risk allow and the valid review approval can yield
`would_execute=true`. Invalid check input is a separate expected failure; it
does not produce a decision or an audit report.

## Optional Claude Code hook adapter

`oteru_gate.claude_pretool` adapts an explicit local policy to Claude Code's
`PreToolUse` event. It is opt-in and runs only when registered in Claude Code
settings, following Claude Code's documented
[`PreToolUse` decision contract](https://code.claude.com/docs/en/hooks#pretooluse-decision-control).
Start with the example policy in
[`fixtures/claude-pretool-policy.json`](fixtures/claude-pretool-policy.json).
The policy supports exact tool names and exact top-level `tool_input` fields;
it does not parse shell commands or infer consequence from arbitrary inputs.

Copy the example policy to a local file you control, then add this complete
settings object to `.claude/settings.local.json` or your user settings. Replace
the policy path with the file you reviewed. The example policy contains only
synthetic commands:

```bash
mkdir -p .claude
cp oteru_gate/fixtures/claude-pretool-policy.json .claude/oteru-policy.local.json
```

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3",
            "args": [
              "${CLAUDE_PROJECT_DIR}/oteru_gate/claude_pretool.py",
              "--policy",
              "${CLAUDE_PROJECT_DIR}/.claude/oteru-policy.local.json"
            ]
          }
        ]
      }
    ]
  }
}
```

The adapter maps matching rules as follows:

- `deny` returns Claude Code's `permissionDecision=deny` and blocks that tool
  call.
- `review` returns `permissionDecision=ask`, which requests confirmation in
  the current Claude Code session.
- `allow`, or no matching rule, emits no decision. Claude Code's own permission
  rules still decide whether the call proceeds.
- If matching rules disagree, the most restrictive decision wins
  (`deny` > `review` > `allow`), independent of file order.
- Missing, oversized, malformed or unsupported policy, invalid hook input, or
  an invalid rule returns a stable `deny` response. The policy is limited to
  64 KiB and 128 rules; the hook payload is limited to 256 KiB.

The local prompt is not authenticated identity or centralized approval. This
adapter does not consume findings from #63/#64/#66, write Oteru audit records,
or control runtimes other than Claude Code. Exact rules are operator-authored;
they are not a detector of action consequence. Keep secrets out of policy
files, especially when using project settings that may be committed. The
installed Claude Code CLI on the authoring machine was 2.1.221; unit tests
exercise the JSON contract, not an interactive Claude session. Validate
`review` in the supported interactive surface before relying on it
operationally. A missing interpreter or disabled/unloaded hook is outside the
adapter's control; this is not a tamper-resistant enterprise gate.

## Boundaries, decisions still needed and rollback

The module does not configure collection, scrub telemetry, detect risk, pause
agents, or authorize an executor. The source branches for #63/#64/#66 and
#21/#67 are independent; this slice consumes synthetic results so it can be
reviewed without implying those integrations already exist. Real deployment
requires agreed check schema/provenance, coverage freshness, trusted action
binding, an IdP, an intercepting executor, durable decisions, revocation,
concurrency handling, audit retention/ACL and operational timeout ownership.
The 300-second default and `reviewer-demo` are demonstration values.

To roll back the slice, remove the explicit `make demo-human-gate` invocation or
revert the functional commit. No default collector/emitter behavior, migrations
or persistent services are changed. Keep any generated audit files needed for
review; rollback does not delete them.
