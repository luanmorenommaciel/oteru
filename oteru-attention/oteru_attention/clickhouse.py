"""Read-only, bounded queries against the Collector's ClickHouse tables."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class StoreUnavailable(RuntimeError):
    """The telemetry backend did not answer a read-only query."""


@dataclass(frozen=True)
class StoreConfig:
    url: str = "http://127.0.0.1:8123"
    user: str = "otel"
    password: str = "otel"
    timeout_seconds: float = 5.0


class ClickHouseStore:
    def __init__(self, config: StoreConfig):
        self.config = config

    def query(self, sql: str) -> list[dict]:
        """Run a fixed SELECT and decode JSONEachRow without logging row contents."""
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("only SELECT queries are supported")
        token = base64.b64encode(f"{self.config.user}:{self.config.password}".encode()).decode(
            "ascii"
        )
        request = Request(
            self.config.url.rstrip("/") + "/?wait_end_of_query=1",
            data=sql.encode("utf-8"),
            headers={"Authorization": f"Basic {token}", "Content-Type": "text/plain"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return [json.loads(line) for line in response if line.strip()]
        except HTTPError as exc:
            raise StoreUnavailable(f"ClickHouse respondeu HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise StoreUnavailable("ClickHouse não está acessível") from exc

    def observations(
        self, *, hours: int, limit: int, demo: bool = False
    ) -> tuple[list[dict], bool]:
        """Read only fields needed by the product; never select prompt or identity values.

        The known, redacted historical capture predates an explicit synthetic
        resource marker. It is classified in SQL, without returning user.email.
        Normal mode excludes both that capture and marked synthetic traffic.
        """
        if not 1 <= hours <= 168 or not 1 <= limit <= 10000:
            raise ValueError("hours or limit outside supported bounds")
        upper = "now64(9) + INTERVAL 1 DAY" if demo else "now64(9)"
        legacy_sample = "LogAttributes['user.email'] = 'user@example.com'"
        traffic = "ResourceAttributes['oteru.traffic'] = 'synthetic'"
        sample_filter = "" if demo else f"AND NOT ({traffic} OR {legacy_sample})"
        log_sql = f"""
SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms,
       'log' AS source, ServiceName AS service,
       if(LogAttributes['session.id'] != '',
          LogAttributes['session.id'], LogAttributes['conversation.id']) AS session_id,
       LogAttributes['agent.id'] AS agent_id,
       LogAttributes['event.name'] AS event,
       if(LogAttributes['tool_use_id'] != '',
          LogAttributes['tool_use_id'], LogAttributes['call_id']) AS tool_use_id,
       LogAttributes['tool_name'] AS tool_name,
       LogAttributes['decision'] AS decision,
       LogAttributes['success'] AS success,
       LogAttributes['from_mode'] AS from_mode,
       LogAttributes['to_mode'] AS to_mode,
       LogAttributes['error_type'] AS error_type,
       LogAttributes['cost_usd'] AS cost_usd,
       TraceId AS trace_id, SpanId AS span_id,
       if({traffic} OR {legacy_sample}, 'sample', 'observed') AS provenance
FROM otel.otel_logs
WHERE Timestamp >= now64(9) - INTERVAL {hours} HOUR
  AND Timestamp <= {upper}
  {sample_filter}
ORDER BY Timestamp DESC LIMIT {limit + 1} FORMAT JSONEachRow
"""
        # Only spans on the GenAI/MCP integration surface identify agent work.
        # Internal application spans remain queryable in ClickHouse but do not
        # become Oteru agent sessions or attention events by implication.
        trace_filter = "" if demo else "AND ResourceAttributes['oteru.traffic'] != 'synthetic'"
        trace_sql = f"""
SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms,
       'trace' AS source, ServiceName AS service,
       if(SpanAttributes['gen_ai.conversation.id'] != '',
          SpanAttributes['gen_ai.conversation.id'],
          SpanAttributes['conversation.id']) AS session_id,
       SpanAttributes['gen_ai.agent.id'] AS agent_id,
       if(SpanAttributes['gen_ai.operation.name'] != '',
          SpanAttributes['gen_ai.operation.name'], SpanAttributes['mcp.method.name']) AS event,
       SpanAttributes['gen_ai.tool.call.id'] AS tool_use_id,
       SpanAttributes['gen_ai.tool.name'] AS tool_name,
       '' AS decision, '' AS success, '' AS from_mode, '' AS to_mode,
       SpanAttributes['error.type'] AS error_type,
       '' AS cost_usd, TraceId AS trace_id, SpanId AS span_id,
       StatusCode AS status_code,
       if(ResourceAttributes['oteru.traffic'] = 'synthetic',
          'sample', 'observed') AS provenance
FROM otel.otel_traces
WHERE Timestamp >= now64(9) - INTERVAL {hours} HOUR
  AND Timestamp <= {upper}
  AND (SpanAttributes['gen_ai.operation.name'] != ''
       OR SpanAttributes['mcp.method.name'] != '')
  {trace_filter}
ORDER BY Timestamp DESC LIMIT {limit + 1} FORMAT JSONEachRow
"""
        logs = self.query(log_sql)
        traces = self.query(trace_sql)
        truncated = len(logs) > limit or len(traces) > limit
        return logs[:limit] + traces[:limit], truncated
