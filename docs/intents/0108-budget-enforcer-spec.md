# Budget enforcer specification

- **Issue:** [#108](https://github.com/luanmorenommaciel/oteru/issues/108)
- **Status:** Security review required; runtime implementation prohibited
- **Captain:** Vinicius Sena (`viniciussena`)
- **Security reviewer:** Silmara (`silmarahp`)

## Scope

The future `oteru-budget-enforcer` is a provider gateway evaluated after the alert-only budget model is stable. This specification authorizes design and shadow measurement only. It does not authorize production blocking.

## Identity and tenancy

1. The team and project identity comes from a gateway credential issued for exactly one pair.
2. A request header cannot select or replace the credential scope.
3. Missing, invalid, revoked or mismatched credentials are refused without exposing budget or consumption.
4. The gateway reads `budget_status_current` through the dedicated read-only `oteru_budget_reader` role.

## Data minimization

- The provider key remains in a secret store and never enters events, logs or metrics.
- Request bodies, prompts, tool content and responses are neither logged nor persisted.
- Decision records contain only an opaque request ID, credential scope, provider, policy version, outcome, stable reason code, latency, `is_stale` and optional override ID.
- The PII policy is applied before any persistent buffer.

## Availability and bypass

- Gateway or budget-reader unavailability is fail-open with `is_stale=true`, an alert and a measured SLO.
- Invalid authentication is not an availability failure and remains denied.
- Direct calls to the provider are a documented bypass. Vigia compares provider-side usage with gateway decisions and alerts on unmatched traffic.
- Refusals never reveal another team or project's budget, thresholds or usage.

## Delivery gates

1. Captain accepts ADR-004 and the policy schema.
2. Silmara records the security review and threat-model disposition.
3. Maestro approves the incremental cost.
4. Shadow mode runs against representative traffic without changing outcomes.
5. Latency percentiles, availability, false positives and bypass coverage meet declared SLOs.
6. Rollback from canary to alert-only is tested.
7. Vinicius records explicit approval in #108 before `enforce` can be enabled.

## Rollout

`spec → security review → cost review → shadow → canary fail-open → explicit approval → enforce`. Each transition is versioned and auditable. A failed gate returns the rollout to the preceding non-blocking state.

## Acceptance evidence

- Threat model with credential theft, cross-tenant access, key leakage, replay, stale budget, outage and direct-provider bypass.
- Reproducible latency and availability benchmark with raw results.
- Shadow report containing aggregate decisions and false-positive analysis, without content.
- Role and secret-store configuration review.
- Rollback exercise and incident runbook.
