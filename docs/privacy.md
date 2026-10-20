# Collector privacy policy (issues #21 and #67)

This V1 completes the Collector-before-storage scrubbing slice of #67. It partially addresses #21 with configurable `hash`, `mask` and `drop` for the documented identity fields and fixed `reject` rules for unsafe telemetry. The `encrypt` strategy and key lifecycle remain open in #21; this release does not implement encryption or claim customer-selected policy on a shared receiver.

The Collector applies a versioned policy **before** the `debug`, `file`, ClickHouse, and ClickStack exporters. Each installation has one operator-controlled policy at `oteru-collector/privacy/policy.json`. OTLP attributes cannot select or weaken it. The `privacy-policy` Compose service compiles the bounded JSON contract into Collector processors and must complete successfully before a new Collector starts. It has no network access; values in the policy cannot contain OTTL code or arbitrary selectors.

This is a prospective, field-based control. It does not sanitize previously saved `telemetry.json` files or rotations, Docker logs, ClickHouse rows, or external copies. Inventory and purge of historical data require a separate authorized operation. Transport, process memory, and direct traffic to a different collector are outside this boundary.

## Decisions and behavior

| Topic | Contract | Reason and trade-off |
| --- | --- | --- |
| Tenant selection | One policy per dedicated Collector installation. | An untrusted OTLP `tenant.id` cannot select a weaker policy; shared-receiver multitenancy is deferred. |
| `hash` | Replace a nonempty string with `hmac:v1:<lowercase hex HMAC-SHA256>`. The UTF-8 HMAC input is `oteru-pii:v1|<field_name>|<exact_value>`; the key is `OTERU_PII_HMAC_KEY` (factory-bound, never in OTTL or config). No case folding or normalization. | Default for all six cataloged identity fields. Stable for the same field, value, and key across signals and Collector restarts; different fields have separate domains. This supports per-field trends but does not consolidate a person's aliases. |
| `mask` | Replace the entire selected AnyValue with literal `REDACTED` in logs and traces. | Loses identity-level correlation. Metrics containing a field configured as `mask` are rejected rather than silently merging series. |
| `drop` | Remove the selected attribute in logs and traces. | Technical name for the removal interpretation of `ignore` in #21. Metrics containing a field configured as `drop` are rejected rather than silently merging series. |
| `reject` | Discard the affected unsafe telemetry item instead of exporting it. | Fixed rule for empty/non-string values under `hash`, metric dimensions that cannot be transformed safely, exemplars and spans with links; not a selectable identity-field action. A rejected resource/scope also removes its descendants. Benign controls in separate resources in the same batch continue through the pipeline. |
| Principal identity | `user.email`, `user.id`, `user.account_uuid`, `user.account_id`, `organization.id`, `host.name` in resource, scope, log, span, span-event and metric datapoint attributes. | Each field can use `hash`, `mask` or `drop`. Only nonempty strings can be hashed; invalid types are rejected before transformation. |
| Known content | Remove `user_prompt`, `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`, `gen_ai.output.messages` in the covered attribute contexts. | Oteru's `user_prompt` is included. This is separate from principal identity policy. |
| Operational log body | Preserve. | Keeps diagnostic events useful. A body containing personal data remains personal data; the policy does **not** claim to sanitize free text. Set `content.log_body` to `drop` to replace the entire body with `REDACTED`. |
| Correlation | Preserve `session.id`, `prompt.id`, trace/span IDs and metric values/timestamps. | Retains joins, but these IDs can link records to a person; access and retention still matter. Email can no longer distinguish live from replayed traffic. |
| Metrics | Hash cataloged identity dimensions at resource, scope and datapoint level before the safety filter. Reject dimensions configured as `mask`/`drop`, known content dimensions and datapoints with exemplars. | Avoids merging distinct series while retaining per-pseudonym cost, volume and trend. The committed sample's **934/934 datapoints** survive under the default policy; a black-box comparison checks each metric name, value, timestamp, temporality, monotonicity and non-sensitive dimension. |
| Span links | Reject spans with links before transformation. | The pinned Collector's selected contexts do not scrub link attributes; rejecting the span can orphan children. That loss is observable and preferable to forwarding an uninspected link. |
| Failures | Unknown/missing/invalid policy prevents new Collector startup. A missing, empty, or shorter-than-32-byte `OTERU_PII_HMAC_KEY` also fails processor Create (ingestion halts; Vigia alerts on the down collector). Processing errors propagate and drop affected telemetry. | No passthrough fallback. Diagnostics run at info level; do not enable OTTL debug tracing on live data. The HMAC key never appears in errors, config dumps, or OTTL text. |
| `encrypt` | Unsupported and rejected. | Recovery purpose and authority, key owner, KMS, rotation, availability and deletion need an architecture decision. |

The catalog is explicit. Unknown keys, names, descriptions, status messages, URLs and arbitrary nested content can contain PII and are **not** claimed to be protected. `content.known_fields=preserve` is available only as an explicit operator decision; it allows the listed content to be exported. Identity fields accept only `hash`, `mask` or `drop`; `reject` applies to the fixed unsafe structures above. No semantic detection, customer DSL, encryption, UI, hot reload, or historical remediation is included.

HMAC-SHA256 here is **pseudonymization, not anonymization**. Outputs remain linkable across records for the same key. Without the key, candidate emails cannot be verified against stored pseudonyms. Treat both the key and the pseudonyms as sensitive data for access and retention decisions. Reversible recovery still needs a future encryption design or token vault.

The `v1` in `hmac:v1:` is the **key version**, not a crypto-algorithm version. Rotating `OTERU_PII_HMAC_KEY` changes every `actor_id` and breaks per-actor series in `fct_cost_actor_daily`. Rotation therefore requires bumping the version prefix (for example to `hmac:v2:`) **and prior notice**. A missing, empty, or short key stops the Collector: ingestion halts and Vigia should alert.

The Collector is pinned to contrib **0.155.0** by digest, plus the `hmacconverter` transform factory (`github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter`) that registers `HMACSHA256(value)`. The policy contract is version **2**. Changing the contract or the key version requires explicit migration. The compiler uses the Python standard library, rejects duplicate or unexpected JSON keys, and publishes a single JSON processor map atomically. Four Collector profiles reference that same map; the privacy tests fail if any of their twelve pipelines omits a required processor. Do not edit generated `processors.json` manually. The HMAC key is read only from the environment at processor Create; it is never written into processors.json.

## Activate or change a policy

1. Review `oteru-collector/privacy/policy.json` and record the intended change. Never place real identity, credentials or arbitrary OTTL inside it.
2. Run `make privacy-config-check` and `scripts/check_signals_e2e.sh` after `make setup`. These tests use fabricated data. The e2e script needs a running Collector+ClickHouse (`make up-clickhouse`) and, for fail-closed HMAC checks, the local `oteru-collector:0.155.0` image. An unavailable runtime is a failed check, not a skip.
3. For a first start, use `make up`, `make up-clickhouse`, `make up-clickstack`, `make up-clickstack-local` or `make up-hyperdx` as usual. For an existing installation, use `make apply-privacy PROFILE=base|clickhouse|clickstack|clickstack-local|hyperdx`. The command **stops the Collector first**, compiles the policy in an isolated container, then recreates only the Collector. Its failure leaves ingestion stopped. The backends must already be running.
4. Confirm the compiler's contract version and SHA-256 of the canonical policy in its logs, check Collector startup and the expected fields in new, synthetic telemetry. Retain the reviewed policy and checksum in the deployment record. There is no hot reload: changing JSON on disk while a Collector runs does not update its active policy.

For rollback, restore the last reviewed **sanitizing** policy and run the same `apply-privacy` command. If no safe policy is available, leave ingestion stopped. Do not roll back to the pre-processor Collector configuration. Existing persisted data remains unchanged by a policy switch.

## Verification and pending decisions

`make privacy-config-check` checks the compiler contract and topology. `scripts/check_signals_e2e.sh` (`make e2e-signals`) replays tagged captures into the running Collector and asserts signal selection, `otel.stg_api_request` totals, `actor_id` matching `^hmac:v1:[0-9a-f]{64}$`, no `@` in any staging column, same HMAC key producing the same id, different keys producing different ids, and that an unset, empty, or 31-byte `OTERU_PII_HMAC_KEY` fails Collector start (non-zero, logged) without the key string appearing in logs. `make test`, `make lint` and `make pii-guard` remain regression gates.

### Reproducible privacy demo

From the repository root, run `make setup` once, then `make demo-privacy` with Docker running. The command starts isolated Collector, ClickHouse and OTLP receiver containers, sends only synthetic telemetry and the committed redacted reference sample, and runs black-box checks. It needs no API keys, existing stack or production data. A failure returns a nonzero exit code; a pass means each assertion below held:

| Destination or behavior | Evidence checked |
| --- | --- |
| Collector `debug` output and local `file` exporter | Original identity is absent; `hash` has independent known HMAC-SHA256 vectors, `mask` yields `REDACTED`, and `drop` removes the field. Two principals keep distinct, stable pseudonyms across signals and restarts. |
| Native ClickHouse exporter | Logs, traces and metrics contain no original cataloged identity. Queries group synthetic cost and volume by two pseudonyms and time bucket. |
| OTLP forwarding to remote and local ClickStack profiles | An isolated OTLP receiver captures logs, traces and metrics from both profiles without original cataloged identity, including the default hash case. This proves the Collector's outgoing payload, not a live ClickStack installation. |
| Reference metric capture | Exactly 934 datapoints survive; every name, value, timestamp, temporality, monotonicity and non-sensitive dimension matches the input point by point. |
| Unsafe span links and metric exemplars | The unsafe record is rejected, a benign control arrives, and debug/file output contains no original canary. |
| Policy update and invalid configuration | A valid policy change takes effect after Collector recreation; an invalid policy leaves ingestion stopped rather than exporting unprocessed data. |

`scripts/check_signals_e2e.sh` is the runtime gate for HMAC `actor_id` shape, key stability, fail-closed startup, and the cost-fixture staging totals. It does not change the policy of an existing stack.

`make up-direct` and `make ingest` send telemetry straight to ClickStack without traversing the Oteru Collector. The Collector policy does not protect that mode; use a Collector-backed profile for data requiring this control.

Before a production rollout, define acceptable event loss and latency/CPU/memory limits and measure them with representative traffic. Decide whether to expand the content catalog, remove correlation IDs, support span links/exemplars safely, implement shared tenancy, or design encrypted values and authorized recovery. None of those are implied by this release.
