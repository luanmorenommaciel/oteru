# oteru-dbt

dbt project for cost aggregation (#45, #47).

## Quick Start

```bash
# Install (Python <= 3.13; Python 3.14 breaks dbt). Tested: dbt-core 1.10.23 and 1.12.6
pip install "dbt-core>=1.10,<1.13" "dbt-clickhouse~=1.10.3"
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
- **`dbt build`: PASS=35** (models + tests) ✓

See `../docs/intents/proof/dbt-build-output.txt` for full proof.

## Notes

- Python 3.14 is not supported by dbt yet and fails at import time; use Python 3.13.

- Uses `dedup_key` (request_id or MD5 hash when empty)
- `cost_lookback_hours` var set to 48h (configurable)
- PII test detects test fixture emails (expected)
- Depends on `otel.stg_api_request` view (created by Duto)
