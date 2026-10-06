-- otel.mcp_calls — MCP calls as first-class rows (#42).
--
-- One row per MCP span (any span carrying mcp.method.name), with the MCP
-- semantic-convention attributes lifted out of the SpanAttributes map into
-- typed columns: which method, which tool / resource / prompt, which session,
-- whether it failed and how long it took. Client and server sides of the same
-- call are both kept — `side` tells them apart, `request_id` joins them.
--
-- A plain (non-materialized) view: no storage, no backfill, always current
-- with otel.otel_traces, and safe to re-apply (`make views`).
CREATE OR REPLACE VIEW otel.mcp_calls AS
SELECT
    Timestamp                                   AS timestamp,
    TraceId                                     AS trace_id,
    SpanId                                      AS span_id,
    ParentSpanId                                AS parent_span_id,
    ServiceName                                 AS service_name,
    if(SpanKind = 'Server', 'server', 'client') AS side,
    SpanAttributes['mcp.method.name']           AS method,
    SpanAttributes['gen_ai.tool.name']          AS tool_name,
    SpanAttributes['mcp.resource.uri']          AS resource_uri,
    SpanAttributes['gen_ai.prompt.name']        AS prompt_name,
    SpanAttributes['mcp.session.id']            AS session_id,
    SpanAttributes['jsonrpc.request.id']        AS request_id,
    SpanAttributes['mcp.protocol.version']      AS protocol_version,
    StatusCode = 'Error'                        AS failed,
    SpanAttributes['error.type']                AS error_type,
    StatusMessage                               AS error_message,
    Duration / 1e6                              AS duration_ms,
    ResourceAttributes                          AS resource_attributes
FROM otel.otel_traces
WHERE SpanAttributes['mcp.method.name'] != '';
