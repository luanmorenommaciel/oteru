FINAL proof for #45/#47 on binary ClickHouse 26.11.1.328
Code SHA: e279c1f
Branch: cursor/cost-ttl-empty-incr-860e (rebased on 0f83418)
Hook: log_run_audit on-run-end ON
dbt build: exit 0, PASS=35 WARN=0 ERROR=0 SKIP=0

Files:
  dbt-build-e279c1f.txt
  dedup-totals-e279c1f.txt          8 rows / 682950 micros
  idempotency-e279c1f.txt           2 incremental runs, zero diff
  midnight-boundary-e279c1f.txt     23:59:59 -> 2026-10-19; 00:00:00 -> 2026-10-20
  late-events-e279c1f.txt           within 48h lands on day; beyond 48h in events_dropped_late=1
  pii-and-equality-e279c1f.txt      test_no_pii PASS; actor==daily PASS; structure PASS
  show-create-actor-e279c1f.txt     TTL day_utc + toIntervalDay(90)
  system-tables-e279c1f.txt         daily no TTL / no actor_id; actor has TTL 90 DAY
  empty-table-incremental-e279c1f.txt  PASS 682950==682950==682950
  ttl-expiry-e279c1f.txt            permanent daily unchanged after actor truncate
  audit-sample-e279c1f.txt
  reconciliation-e279c1f.txt
  freshness-e279c1f.txt             on-time vs late
  check-cost-retention-e279c1f.txt  all ok, no SKIP, check-cost-retention: OK
