# Threat model — budget enforcer

| Threat | Preventive control | Detection | Required evidence |
| --- | --- | --- | --- |
| Header impersonates another tenant | Credential-bound team × project; header ignored | Authentication failures by reason code | Cross-tenant negative tests |
| Credential theft or replay | Short-lived, revocable credential and nonce/replay control | Reuse and geographic anomaly alert | Rotation and revocation exercise |
| Provider key disclosure | Secret store; no key in process logs or telemetry | Secret scanning and access audit | Gitleaks plus secret-store policy |
| Prompt or response disclosure | Body never logged or persisted | Schema and log-content tests | PII scan of logs, errors and DLQ |
| Stale or unavailable budget | Fail-open warning with `is_stale` and SLO | Vigia availability alert | Failure-injection report |
| Cross-tenant budget disclosure | Generic refusal and scoped read-only query | Denied-query audit | Response snapshot tests |
| Direct provider bypass | Documented network path and provider usage reconciliation | Unmatched-usage alert | Bypass simulation |
| Unauthorized enforcement | Disabled runtime flag until explicit Captain approval | Configuration-change audit | Approval link in #108 |

## Review disposition

Silmara must record `approved`, `changes_requested` or `rejected` with evidence references. Until the disposition is `approved`, the only permitted execution mode is offline specification validation and non-blocking shadow measurement.
