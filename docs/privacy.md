# Collector privacy policy (issues #21 and #67)

This V1 completes the Collector-before-storage scrubbing slice of #67. It partially addresses #21 with configurable `mask` and `drop` for the documented identity fields and fixed `reject` rules for unsafe telemetry. The `encrypt` strategy and key lifecycle remain open in #21; this release does not implement encryption or claim customer-selected policy on a shared receiver.

The Collector applies a versioned policy **before** the `debug`, `file`, ClickHouse, and ClickStack exporters. Each installation has one operator-controlled policy at `oteru-collector/privacy/policy.json`. OTLP attributes cannot select or weaken it. The `privacy-policy` Compose service compiles the bounded JSON contract into Collector processors and must complete successfully before a new Collector starts. It has no network access; values in the policy cannot contain OTTL code or arbitrary selectors.

This is a prospective, field-based control. It does not sanitize previously saved `telemetry.json` files or rotations, Docker logs, ClickHouse rows, or external copies. Inventory and purge of historical data require a separate authorized operation. Transport, process memory, and direct traffic to a different collector are outside this boundary.

## Decisions and behavior

| Topic | Contract | Reason and trade-off |
| --- | --- | --- |
| Tenant selection | One policy per dedicated Collector installation. | An untrusted OTLP `tenant.id` cannot select a weaker policy; shared-receiver multitenancy is deferred. |
| `mask` | Replace the entire selected AnyValue with literal `REDACTED`. | Deterministic and irreversible; values can no longer distinguish users. |
| `drop` | Remove the selected attribute. | Technical name for the removal interpretation of `ignore` in #21. Unknown action names, including `ignore`, are rejected to avoid ambiguity. |
| `reject` | Discard the affected telemetry item instead of exporting a partially inspected or misleading result. | This is a fixed rule for sensitive metrics, exemplars and spans with links, not a selectable identity-field action. The loss of those records is intentional and observable. |
| Principal identity | `user.email`, `user.id`, `user.account_uuid`, `user.account_id`, `organization.id`, `host.name` in resource, scope, log, span and span-event attributes. | The default is mask; each key can be changed to drop. Empty, numeric, boolean, array and map values are covered by key presence. |
| Known content | Remove `user_prompt`, `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`, `gen_ai.output.messages` in the covered attribute contexts. | Oteru's `user_prompt` is included. This is separate from principal identity policy. |
| Operational log body | Preserve. | Keeps diagnostic events useful. A body containing personal data remains personal data; the policy does **not** claim to sanitize free text. Set `content.log_body` to `drop` to replace the entire body with `REDACTED`. |
| Correlation | Preserve `session.id`, `prompt.id`, trace/span IDs and metric values/timestamps. | Retains joins, but these IDs can link records to a person; access and retention still matter. Email can no longer distinguish live from replayed traffic. |
| Metrics | Reject a metric with any protected key at resource, scope or datapoint level. Reject datapoints with exemplars. | Replacing labels could merge distinct series. The entire affected metric or datapoint is discarded; harmless metrics still pass. The committed sample has **934/934 datapoints** with protected identity and all will be rejected under the default. This was counted from `oteru-emitter/samples/telemetry-sample.json` on 2026-10-13. This is an explicit loss of current metric visibility pending a future privacy-preserving metric design. |
| Span links | Reject spans with links before transformation. | The pinned Collector's selected contexts do not scrub link attributes; rejecting the span can orphan children. That loss is observable and preferable to forwarding an uninspected link. |
| Failures | Unknown/missing/invalid policy prevents new Collector startup; processing errors propagate and drop affected telemetry. | No passthrough fallback. Diagnostics run at info level; do not enable OTTL debug tracing on live data. |
| `encrypt` | Unsupported and rejected. | Recovery purpose and authority, key owner, KMS, rotation, availability and deletion need an architecture decision. |

The catalog is explicit. Unknown keys, names, descriptions, status messages, URLs and arbitrary nested content can contain PII and are **not** claimed to be protected. `content.known_fields=preserve` is available only as an explicit operator decision; it allows the listed content to be exported. Identity fields accept only `mask` or `drop`; `reject` applies to the fixed unsafe structures above. No semantic detection, customer DSL, encryption, UI, hot reload, or historical remediation is included.

The Collector is fixed to contrib **0.155.0** by digest. The compiler uses the Python standard library, rejects duplicate or unexpected JSON keys, and publishes a single JSON processor map atomically. Four Collector profiles reference that same map; the privacy tests fail if any of their twelve pipelines omits a required processor. Do not edit generated `processors.json` manually.

## Activate or change a policy

1. Review `oteru-collector/privacy/policy.json` and record the intended change. Never place real identity, credentials or arbitrary OTTL inside it.
2. Run `make privacy-config-check` and `make e2e-privacy` after `make setup`. These tests use fabricated data; Docker is required for E2E. An unavailable Docker daemon is a failed check.
3. For a first start, use `make up`, `make up-clickhouse`, `make up-clickstack`, `make up-clickstack-local` or `make up-hyperdx` as usual. For an existing installation, use `make apply-privacy PROFILE=base|clickhouse|clickstack|clickstack-local|hyperdx`. The command **stops the Collector first**, compiles the policy in an isolated container, then recreates only the Collector. Its failure leaves ingestion stopped. The backends must already be running.
4. Confirm the compiler's contract version and SHA-256 of the canonical policy in its logs, check Collector startup and the expected fields in new, synthetic telemetry. Retain the reviewed policy and checksum in the deployment record. There is no hot reload: changing JSON on disk while a Collector runs does not update its active policy.

For rollback, restore the last reviewed **sanitizing** policy and run the same `apply-privacy` command. If no safe policy is available, leave ingestion stopped. Do not roll back to the pre-processor Collector configuration. Existing persisted data remains unchanged by a policy switch.

## Verification and pending decisions

`make privacy-config-check` checks the compiler contract and topology. `make e2e-privacy` starts isolated containers on random loopback ports and checks HTTP/gRPC, mask/drop, content options, logs/traces/metrics, all four config profiles, stdout/file/ClickHouse/OTLP forwarding, invalid activation and deliberate bypass. It observes both absence of protected canaries and delivery of benign controls. `make test`, `make lint`, `make pii-guard` and `make e2e-signals` remain regression gates. `make e2e-signals` uses a benign metric fixture because the old fixture's identity-labeled metric must now be rejected.

### Reproducible privacy demo

From the repository root, run `make setup` once, then `make demo-privacy` with Docker running. The command starts isolated Collector, ClickHouse and OTLP receiver containers, sends only synthetic telemetry containing `privacy-canary@example.com`, and runs seven black-box checks. It needs no API keys, existing stack or production data. A failure returns a nonzero exit code; a pass means each assertion below held:

| Destination or behavior | Evidence checked |
| --- | --- |
| Collector `debug` output and local `file` exporter | The original canary is absent; identity is `REDACTED` under `mask` and absent under `drop`; benign correlation and operational body survive. |
| Native ClickHouse exporter | Queries of logs, traces and metrics tables contain no original canary; one benign record per signal arrives. |
| OTLP forwarding to ClickStack | An isolated OTLP receiver standing in for the downstream endpoint captures logs, traces and metrics without the original canary; benign records arrive. This proves the Collector's outgoing payload, not a live ClickStack installation. |
| Unsafe span links and metric exemplars | The unsafe record is rejected, a benign control arrives, and debug/file output contains no original canary. |
| Policy update and invalid configuration | A valid policy change takes effect after Collector recreation; an invalid policy leaves ingestion stopped rather than exporting unprocessed data. |

The broader `make e2e-privacy` suite also exercises content options, every configured signal/context and HTTP/gRPC transport, topology bypass detection, and validation of every Collector profile. The demo and suite use the same assertions; neither changes the policy of an existing stack.

`make up-direct` and `make ingest` send telemetry straight to ClickStack without traversing the Oteru Collector. The Collector policy does not protect that mode; use a Collector-backed profile for data requiring this control.

Before a production rollout, define acceptable event loss and latency/CPU/memory limits, measure them with representative traffic, and decide how to recover useful metrics without merging series. Also decide whether to expand the content catalog, remove correlation IDs, support span links/exemplars safely, implement shared tenancy, or design encrypted values and authorized recovery. None of those are implied by this release.
