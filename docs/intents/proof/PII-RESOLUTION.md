# PII Test Resolution - Final Report

## Issue
PII test (`test_no_pii.sql`) was **FAILING with 8 violations** on original fixture.

## Root Cause
**Column:** `int_api_request_dedup.actor_id`  
**Source:** `otel.stg_api_request` view, line 27: `LogAttributes['user.email'] AS actor_id`

**Original fixture values** (pre-fix):
- `user@example.com` (6 occurrences)
- `test@example.org` (2 occurrences)

The view correctly passes through `LogAttributes['user.email']` as-is. The problem was that the **fixture contained raw email addresses** instead of pseudonymized values.

## PII Test Specification (UNCHANGED)
```sql
WHERE positionCaseInsensitive(value, '@') > 0
```
**Constraint:** ANY '@' symbol in ANY column → FAIL  
**Status:** Test logic **NOT modified** (as required)

## The Fix (Teammate Commit 5e9f55c)
**Action:** Replace raw emails in fixture with `sha256:v1:<hex>` pseudonyms matching PR #94 format

**Fixed fixture values:**
- Actor 1: `sha256:v1:dd5cefde2a9c1825d42de9ac8af71a5a17d816f89e8cfa5318513a245ca3ad14`
- Actor 2: `sha256:v1:c71cc8fe3782f8d0510586af964334b6930f817fc79bfb460c764f6be9f5d598`

**Verification:**
```bash
$ grep -c '@' oteru-emitter/tests/fixtures/api_request_cost_fixture.json
0
```
✅ **ZERO '@' symbols in entire fixture**

## Data Flow
1. Fixture loads `LogAttributes['user.email'] = "sha256:v1:<hex>"`
2. View reads: `LogAttributes['user.email'] AS actor_id`
3. dbt dedup: `actor_id` column contains `"sha256:v1:<hex>"`
4. PII test scans all string columns for '@'
5. **Result: PASS** (no '@' found anywhere)

## Test Results
```
11 of 33 test test_no_pii ................. PASS
```

All 8 records now have pseudonymized `actor_id` values. No '@' symbols exist in:
- `dedup_key`
- `session_id` 
- `model_raw`
- `actor_id` ✓ (previously contained emails)
- `service_name`

## Impact on Metrics
**NONE** - All cost/count metrics unchanged:
- Staging: 9 rows, 806,400 micros
- After dedup: 8 rows, 682,950 micros
- Totals identical to pre-fix proofs

## Alignment with PR #94
The fixture now correctly simulates PR #94's pseudonymization format:
- Format: `sha256:v1:<lowercase_hex>`
- The view reads this value directly (no transformation needed)
- Production OTLP exporter will provide pseudonymized emails in this format

## Summary
✅ **PII test unchanged** (strict '@' detection preserved)  
✅ **Fixture corrected** (pseudonymized emails loaded)  
✅ **View unchanged** (correctly reads `user.email`)  
✅ **Test now passes** (zero '@' symbols in data)  
✅ **Metrics unchanged** (8 rows, 682,950 micros)
