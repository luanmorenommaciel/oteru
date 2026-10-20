"""Expected values for the api_request_cost_fixture.json test fixture.

These constants define the acceptance criteria for the stg_api_request view
and downstream deduplication. They are computed from the fixture and must be
updated if the fixture changes.
"""

# Fixture composition
TOTAL_EVENTS = 10
SYNTHETIC_EVENTS = 1
NON_SYNTHETIC_EVENTS = 9  # visible in otel.stg_api_request

# Deduplication (one row per dedup_key, earliest observed_ts)
DISTINCT_DEDUP_KEYS = 8  # 2 duplicates with request_id='req_001'
DUPLICATE_REQUEST_IDS = {"req_001": 2}  # request_id -> occurrence count

# Cost aggregation (in USD micros, where 1 USD = 1,000,000 micros)
# Pre-dedup: sum over all 9 non-synthetic rows in stg_api_request
COST_PRE_DEDUP_MICROS = 806_400  # $0.8064

# Post-dedup: sum after keeping one row per dedup_key (earliest observed_ts)
# Breakdown by dedup_key:
#   req_001: 123,450 (duplicate removed)
#   empty_*: 2,000
#   req_005: 250,000
#   req_006: 75,000
#   req_007: 1,000
#   req_008: 150,000
#   req_009: 80,000
#   req_010: 1,500
COST_POST_DEDUP_MICROS = 682_950  # $0.68295

# Special cases
HAS_EMPTY_REQUEST_ID = 1
HAS_LATE_ARRIVAL = 1  # event_ts vs observed_ts > 25 hours
HAS_DAY_BOUNDARY_EVENTS = 2  # one at 23:59:59, one at 00:00:00
