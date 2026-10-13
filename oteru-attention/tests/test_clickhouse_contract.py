from __future__ import annotations

import pytest

from oteru_attention.clickhouse import ClickHouseStore, StoreConfig


class RecordingStore(ClickHouseStore):
    def __init__(self):
        super().__init__(StoreConfig())
        self.queries = []

    def query(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        return []


def test_queries_are_read_only_bounded_and_exclude_replay_in_normal_mode() -> None:
    store = RecordingStore()
    assert store.observations(hours=24, limit=100) == ([], False)
    logs, traces = store.queries
    assert logs.lstrip().startswith("SELECT ")
    assert traces.lstrip().startswith("SELECT ")
    assert "LIMIT 101" in logs and "LIMIT 101" in traces
    assert "Timestamp <= now64(9)" in logs
    assert "AND NOT (" in logs
    assert "user@example.com" in logs  # older documented redacted capture
    assert "LogAttributes['prompt']" not in logs
    assert "LogAttributes['conversation.id']" in logs
    assert "LogAttributes['call_id']" in logs
    assert "SpanAttributes['gen_ai.operation.name'] != ''" in traces
    assert "SpanAttributes['mcp.method.name'] != ''" in traces
    assert "ResourceAttributes['oteru.traffic'] != 'synthetic'" in traces

    store.queries.clear()
    store.observations(hours=24, limit=100, demo=True)
    assert "now64(9) + INTERVAL 1 DAY" in store.queries[0]
    assert "AND NOT (" not in store.queries[0]


@pytest.mark.parametrize("hours,limit", [(0, 1), (169, 1), (1, 0), (1, 10001)])
def test_query_bounds_reject_out_of_range_parameters(hours: int, limit: int) -> None:
    with pytest.raises(ValueError):
        RecordingStore().observations(hours=hours, limit=limit)
