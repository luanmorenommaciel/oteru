# Ingestor benchmark harness

The harness sends the same payload, request count, concurrency and timeout to every implementation and records throughput plus p50/p95/p99 latency.

```bash
python benchmarks/ingestor/benchmark.py \
  --target python=http://127.0.0.1:14318/v1/logs \
  --target go=http://127.0.0.1:24318/v1/logs \
  --target rust=http://127.0.0.1:34318/v1/logs \
  --payload oteru-emitter/tests/fixtures/tiny-capture.json \
  --requests 1000 --concurrency 16 --output benchmark.json
```

CPU/RSS, loss, duplication and cross-platform confidence intervals still require the three equivalent ingestor heads and their containers. Until PRs #87–#89 are integrated and the CRLF healthcheck is fixed, results are exploratory and must not select a default implementation.
