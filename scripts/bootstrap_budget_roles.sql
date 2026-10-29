-- #48 Alicerce: ClickHouse users/roles for budget_status_current (S5) and alerts.
-- Idempotent. No password or credential is stored in this file.
-- CI/local default: IDENTIFIED WITH no_password HOST LOCAL.
-- Production password: the runner substitutes IDENTIFIED WITH no_password HOST LOCAL
-- from OTERU_BUDGET_DEFINER_PASSWORD at runtime (never echoed or logged).
-- Database: otel (dbt profile schema). Least privilege throughout.
-- oteru_budget_alerts_reader is a ROLE, separate from oteru_budget_reader (#108).

CREATE USER IF NOT EXISTS oteru_budget_view_definer
    IDENTIFIED WITH no_password HOST LOCAL;

CREATE ROLE IF NOT EXISTS oteru_budget_alerts_reader;

-- Columns read by models/budget/budget_status_current.sql (no other relation).
GRANT SELECT(
    day_utc,
    period_start,
    scope,
    scope_key,
    team,
    project,
    owner_team,
    budget_usd_micros,
    consumed_period_usd_micros,
    consumed_milli,
    warn_milli,
    breach_milli,
    budget_status,
    action_on_breach,
    last_cost_ts
) ON otel.fct_budget_consumption_daily TO oteru_budget_view_definer;

-- Alerts reader: column-level SELECT only (spec §7.4 / §7.12).
GRANT SELECT ON otel.audit_budget_runs TO oteru_budget_alerts_reader;

GRANT SELECT(
    scope,
    scope_key,
    team,
    project,
    period_start,
    as_of_ts,
    is_stale,
    budget_status,
    action_on_breach
) ON otel.budget_status_current TO oteru_budget_alerts_reader;

GRANT SELECT(
    run_id,
    run_completed_at,
    run_status,
    events_dropped_late
) ON otel.audit_cost_runs TO oteru_budget_alerts_reader;
