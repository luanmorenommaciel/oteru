FINAL proof for #45/#47 on binary ClickHouse 26.11.1.328
Code SHA: e22dbcc
Branch: cursor/cost-ttl-empty-incr-860e (rebased on origin/feat/45-47-cost-aggregates)
Hook: log_run_audit on-run-end ON
Default actor TTL: 90 days (no 1000-day override)
Fixture: load_shifted_cost_fixture.py (request_id/costs unchanged, no restamp)

dbt build: exit 0 (see dbt-build-e22dbcc.txt)
int + both facts: 8 keys / 682950 micros under default 90d TTL
equality FAIL on 0 actor rows (never empty-vs-empty)
SHOW CREATE TABLE: TTL day_utc + toIntervalDay(90)
OTERU_HOT_TTL_TEST=1 make check-cost-retention: see check-cost-retention-e22dbcc.txt
