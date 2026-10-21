# oteru-dbt

dbt project for cost aggregation (#45, #47).

## Quick Start

```bash
# Install
pip install dbt-clickhouse
cd oteru-dbt && dbt deps

# Configure (env vars or .env)
export CLICKHOUSE_HOST=localhost
export CLICKHOUSE_PORT=8123

# Run
dbt build --profiles-dir .
```

## Results (from Duto's fixture)

- **Staging**: 9 rows, 806400 micros (includes duplicate req_001)
- **After dedup**: 8 rows, 682950 micros ✓
- **All 32 tests passing** ✓

See `../docs/intents/proof/dbt-build-output.txt` for full proof.

## Notes

- Uses `dedup_key` (request_id or MD5 hash when empty)
- `cost_lookback_hours` var set to 48h (configurable)
- PII test detects test fixture emails (expected)
- Depends on `otel.stg_api_request` view (created by Duto)
