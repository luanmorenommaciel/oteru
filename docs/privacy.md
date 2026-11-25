# Collector privacy policy (issues #21 and #67)

This V1 completes the Collector-before-storage scrubbing slice of #67 and adds an opt-in `encrypt` action for the documented identity fields in #21. Policy v2 remains compatible and the committed default still hashes identity fields. Policy v3 adds `ignore` (same removal behavior as `drop`) and `encrypt`. Encryption is for one dedicated Collector installation; this is not a multi-tenant policy service.

The Collector applies a versioned policy **before** the `debug`, `file`, ClickHouse, and ClickStack exporters. Each installation has one operator-controlled policy at `oteru-collector/privacy/policy.json`. OTLP attributes cannot select or weaken it. The `privacy-policy` Compose service compiles the bounded JSON contract into Collector processors and must complete successfully before a new Collector starts. It has no network access; values in the policy cannot contain OTTL code or arbitrary selectors.

This is a prospective, field-based control. It does not sanitize previously saved `telemetry.json` files or rotations, Docker logs, ClickHouse rows, or external copies. Inventory and purge of historical data require a separate authorized operation. Transport, process memory, and direct traffic to a different collector are outside this boundary.

## Decisions and behavior

| Topic | Contract | Reason and trade-off |
| --- | --- | --- |
| Tenant selection | One policy per dedicated Collector installation. | An untrusted OTLP `tenant.id` cannot select a weaker policy; shared-receiver multitenancy is deferred. |
| `hash` | Replace a nonempty string with `hmac:v1:<lowercase hex HMAC-SHA-256>`. The exact UTF-8 message is `oteru-pii:v1|<field_name>|<exact_value>`; no case folding or normalization occurs. HMAC uses the UTF-8 bytes of the 64-character `OTERU_PII_HMAC_KEY`. | Default for all six cataloged identity fields. Stable for the same field and exact value while the key remains unchanged; different fields have separate domains. This supports per-field trends but does not consolidate a person's aliases. |
| `mask` | Replace the entire selected AnyValue with literal `REDACTED` in logs and traces. | Loses identity-level correlation. Metrics containing a field configured as `mask` are rejected rather than silently merging series. |
| `drop` | Remove the selected attribute in logs and traces. | Technical name for the removal interpretation of `ignore` in #21. Metrics containing a field configured as `drop` are rejected rather than silently merging series. |
| `ignore` | Remove the selected attribute in logs and traces. | V3 spelling for the existing `drop` behavior. Metrics containing that dimension are rejected. |
| `encrypt` | Replace a cataloged nonempty string with `oteru:enc:v1:<key-id>:<field>:<base64url(nonce+ciphertext+tag)>` using AES-256-GCM and a fresh random nonce. The field name is authenticated as additional data. | V3 only. Supports the six identity fields in resource, scope, log, span and span-event attributes. Keyring absence, invalid data, or encryption errors fail closed. |
| `reject` | Discard the affected unsafe telemetry item instead of exporting it. | Fixed rule for empty/non-string values under `hash`, metric dimensions that cannot be transformed safely, exemplars and spans with links; not a selectable identity-field action. A rejected resource/scope also removes its descendants. Benign controls in separate resources in the same batch continue through the pipeline. |
| Principal identity | `user.email`, `user.id`, `user.account_uuid`, `user.account_id`, `organization.id`, `host.name` in resource, scope, log, span, span-event and metric datapoint attributes. | V2 supports `hash`, `mask`, `drop`; v3 also supports `ignore`, `encrypt`. Only nonempty strings can be hashed or encrypted; invalid types are rejected before transformation. |
| Known content | Remove `user_prompt`, `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`, `gen_ai.output.messages` in the covered attribute contexts. | Oteru's `user_prompt` is included. This is separate from principal identity policy. |
| Operational log body | Preserve. | Keeps diagnostic events useful. A body containing personal data remains personal data; the policy does **not** claim to sanitize free text. Set `content.log_body` to `drop` to replace the entire body with `REDACTED`. |
| Correlation | Preserve `session.id`, `prompt.id`, trace/span IDs and metric values/timestamps. | Retains joins, but these IDs can link records to a person; access and retention still matter. Email can no longer distinguish live from replayed traffic. |
| Metrics | Hash cataloged identity dimensions at resource, scope and datapoint level before the safety filter. Reject dimensions configured as `mask`/`drop`, known content dimensions and datapoints with exemplars. | Avoids merging distinct series while retaining per-pseudonym cost, volume and trend. The committed sample's **934/934 datapoints** survive under the default policy; a black-box comparison checks each metric name, value, timestamp, temporality, monotonicity and non-sensitive dimension. |
| Span links | Reject spans with links before transformation. | The pinned Collector's selected contexts do not scrub link attributes; rejecting the span can orphan children. That loss is observable and preferable to forwarding an uninspected link. |
| Metrics with `encrypt` | Reject datapoints carrying an encrypted identity dimension; other safe datapoints continue. | Ciphertext would create a different time series per event and break useful aggregation. No metric identity value is encrypted. |
| Decrypt | Local `oteru-decrypt` requires terminal output and records effective OS UID, approved purpose, field, key ID, ciphertext SHA-256 fingerprint and outcome to a chained JSONL audit file. It fsyncs the success record before writing plaintext. | Audit write or chain validation failure blocks release. The chain detects edits to retained records but is not independently anchored: a host administrator who can access the key can also alter or remove the log. This is local accountability, not an immutable audit service. |
| Failures | Unknown/missing/invalid policy or required keyring prevents the Collector from starting; processing errors propagate and drop affected telemetry. | No passthrough fallback. Diagnostics run at info level; do not enable OTTL debug tracing on live data. |

The catalog is explicit. Unknown keys, names, descriptions, status messages, URLs and arbitrary nested content can contain PII and are **not** claimed to be protected. `content.known_fields=preserve` is available only as an explicit operator decision; it allows the listed content to be exported. Prompts, tool input/output and the log body are not encrypted; known content fields are dropped by default and the log body is preserved by default. `reject` applies to the fixed unsafe structures above. No semantic detection, customer DSL, UI, hot reload, or historical remediation is included.

HMAC here is **pseudonymization, not anonymization**. Outputs remain linkable across records. A secret key prevents offline candidate enumeration by someone who only has pseudonyms and knows the public domain format; a compromised key removes that protection. The HMAC key must remain secret and stable across restarts. AES-GCM uses a separate installation-local keyring with an active key ID and retained old key IDs for decrypt. New ciphertext uses the active key; remove an old key only after its ciphertext is no longer needed. Changing HMAC keys breaks pseudonym joins; changing/removing encryption keys makes old ciphertext unrecoverable. The keyring file is mounted by Docker Compose as a file-backed secret; this is a read-only bind mount, **not an encrypted vault**. Protect the host file and backups. A host administrator can read the key and alter the local audit log.

The custom Collector distribution is built with OCB **0.155.0** and the `HMACSHA256` and `AESGCMEncrypt` converters from `hmacconverter`. Policy v2 remains supported; v3 adds `ignore` and `encrypt`. The HMAC key is supplied through `OTERU_PII_HMAC_KEY` at runtime and must be exactly 64 lowercase hex characters. The encryption keyring is supplied only when encryption is enabled. The compiler uses the Python standard library, rejects duplicate or unexpected JSON keys, and publishes a single JSON processor map atomically. All Collector profiles reference that same map. Do not edit generated `processors.json` manually.

## Activate or change a policy

1. Review `oteru-collector/privacy/policy.json` and record the intended change. Never place real identity, credentials or arbitrary OTTL inside it.
2. Keep the HMAC key in the gitignored `oteru-collector/.env` or inject it through the environment. For encryption, copy `oteru-collector/privacy/policy.encrypt.example.json` to `privacy/policy.json`, then create a protected keyring JSON file outside the repository with this shape: `{"version":1,"active_key_id":"install-2026-01","keys":{"install-2026-01":"<64 lowercase hex characters>"}}`. The file must have no group/other permissions (for example, mode `0600` or `0400`) and be owned by the UID/GID used by the Collector's `nonroot` user, so the process can read it without making the key group/world-readable. Keep it out of source control, shell history, logs and build arguments. Preserve old key IDs in `keys` during rotation for decrypting existing records.
3. Run `make privacy-config-check` and `make e2e-privacy` after `make setup`. These tests use fabricated data; Docker is required for E2E. An unavailable Docker daemon is a failed check.
4. For a first start with encryption, export `OTERU_PII_KEYRING_FILE` as the absolute path to that protected file and run `make up ENCRYPT=1` (or the matching `make up-clickhouse`, `make up-clickstack`, `make up-clickstack-local` or `make up-hyperdx` target). For an existing installation, run `OTERU_PII_KEYRING_FILE=/absolute/path/keyring.json make apply-privacy PROFILE=base ENCRYPT=1` (replace `base` with the active profile). The command validates the file path, type and permissions before stopping ingestion, builds the distribution, stops the Collector before policy compilation, then recreates only the Collector with the read-only file-backed secret. Invalid keyring contents are rejected by Collector startup and leave ingestion stopped. Without `ENCRYPT=1`, the normal stack does not mount the keyring.
5. Confirm the compiler's contract version and SHA-256 of the canonical policy in its logs, check Collector startup and the expected fields in new, synthetic telemetry. Retain the reviewed policy and checksum in the deployment record. There is no hot reload: changing JSON on disk while a Collector runs does not update its active policy.

For rollback, restore the last reviewed **sanitizing** policy and run the same `apply-privacy` command. If no safe policy is available, leave ingestion stopped. Do not roll back to the pre-processor Collector configuration. Existing persisted data remains unchanged by a policy switch.

## Verification and pending decisions

`make privacy-config-check` checks the compiler contract and topology. `make e2e-privacy` starts isolated containers on random loopback ports and checks HTTP/gRPC, hash/mask/drop/encrypt, content options, logs/traces/metrics, all Collector profiles, stdout/file/ClickHouse/OTLP forwarding, invalid activation and deliberate bypass. It observes both absence of original cataloged values and delivery of benign controls. The encryption runtime case checks all 42 identity/context combinations and verifies that unsafe metric dimensions are rejected while a benign series survives. The suite also compares all 934 reference datapoints and queries synthetic cost, volume and trend for two pseudonyms in ClickHouse.

### Authorized local decrypt

On macOS/Linux, build/test the helper from `oteru-collector/hmacconverter` with `go test ./...`. To decrypt one copied ciphertext, pipe it to `go run ./cmd/oteru-decrypt` while keeping stdout attached to an interactive terminal:

```bash
cd oteru-collector/hmacconverter
printf '%s\n' 'oteru:enc:v1:install-2026-01:user.email:<base64url>' | \
  go run ./cmd/oteru-decrypt \
    --key-file /absolute/path/keyring.json \
    --audit-file /absolute/path/private/oteru-decrypt-audit.jsonl \
    --field user.email --purpose audit-review
```

Use one of `audit-review`, `incident-response`, `customer-request` or `legal-request` and replace the placeholder ciphertext with the actual value. Keep the audit directory private. Plaintext is emitted only after the audit record is appended and fsynced; the command never writes plaintext or ciphertext to the audit. Redirecting stdout or running with a non-terminal output blocks decrypt. The audit chain detects changes to retained records, but the host administrator can still access the key, rewrite the chain or delete the log; export/anchor it elsewhere if stronger evidence is required.

### Reproducible privacy demo

From the repository root, run `make setup` once, then `make demo-privacy` with Docker running. The command starts isolated Collector, ClickHouse and OTLP receiver containers, sends only synthetic telemetry and the committed redacted reference sample, and runs black-box checks. It needs no API keys, existing stack or production data. A failure returns a nonzero exit code; a pass means each assertion below held:

| Destination or behavior | Evidence checked |
| --- | --- |
| Collector `debug` output and local `file` exporter | Original identity is absent; `hash` matches independently computed HMAC-SHA-256 results, `mask` yields `REDACTED`, and `drop` removes the field. Two principals keep distinct, stable pseudonyms across signals and restarts with one test key. |
| Native ClickHouse exporter | Logs, traces and metrics contain no original cataloged identity. Queries group synthetic cost and volume by two pseudonyms and time bucket. |
| OTLP forwarding to remote and local ClickStack profiles | An isolated OTLP receiver captures logs, traces and metrics from both profiles without original cataloged identity, including the default hash case. This proves the Collector's outgoing payload, not a live ClickStack installation. |
| Reference metric capture | Exactly 934 datapoints survive; every name, value, timestamp, temporality, monotonicity and non-sensitive dimension matches the input point by point. |
| Unsafe span links and metric exemplars | The unsafe record is rejected, a benign control arrives, and debug/file output contains no original canary. |
| Policy update and invalid configuration | A valid policy change takes effect after Collector recreation; an invalid policy leaves ingestion stopped rather than exporting unprocessed data. |

The broader `make e2e-privacy` suite also exercises all six cataloged keys in every supported context, empty and unsupported identity values, content options, HTTP/gRPC transport, topology bypass detection, and validation of every Collector profile. The demo and suite use the same assertions; neither changes the policy of an existing stack.

`make up-direct` and `make ingest` send telemetry straight to ClickStack without traversing the Oteru Collector. The Collector policy does not protect that mode; use a Collector-backed profile for data requiring this control.

Before a production rollout, define acceptable event loss and latency/CPU/memory limits and measure them with representative traffic. Decide whether to expand the content catalog, remove correlation IDs, support span links/exemplars safely, implement shared tenancy, or replace the local key custody and audit model with centrally managed controls. Those remain outside this V1.
