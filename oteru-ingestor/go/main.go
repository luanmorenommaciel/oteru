// oteru-ingestor, Go implementation (#25) — same contract as the Python
// reference (../SPEC.md), held to the same black-box suite
// (scripts/check_ingestor.sh).
//
// Decoding and value rendering use the collector's own pdata module, so
// attribute maps come out exactly as the contrib clickhouse exporter writes
// them (pcommon.Value.AsString). Inserts go through ClickHouse's HTTP
// interface with FORMAT JSONEachRow — one INSERT per table per request, like
// the Python one, so the #3 benchmark compares languages, not strategies.
package main

import (
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

var (
	chURL      = strings.TrimRight(env("CLICKHOUSE_URL", "http://clickhouse:8123"), "/")
	chUser     = env("CLICKHOUSE_USER", "otel")
	chPassword = env("CLICKHOUSE_PASSWORD", "otel")
	db         = env("INGEST_DB", "ingest_go")
	port       = env("PORT", "4318")
	schemaPath = env("SCHEMA_PATH", "schema.sql")
	client     = &http.Client{Timeout: 30 * time.Second}
	ready      atomic.Bool
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// errIngest marks a ClickHouse failure — answered as 503 so OTLP clients retry.
var errIngest = errors.New("ingest")

// --- ClickHouse ---------------------------------------------------------------

func clickhouse(query string, body []byte) error {
	var req *http.Request
	var err error
	if body == nil {
		req, err = http.NewRequest(http.MethodPost, chURL, strings.NewReader(query))
	} else {
		req, err = http.NewRequest(http.MethodPost, chURL+"/?query="+urlQuery(query), bytes.NewReader(body))
	}
	if err != nil {
		return fmt.Errorf("%w: %v", errIngest, err)
	}
	req.SetBasicAuth(chUser, chPassword)
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("%w: clickhouse unreachable: %v", errIngest, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 300))
		return fmt.Errorf("%w: clickhouse %d: %s", errIngest, resp.StatusCode, strings.TrimSpace(string(msg)))
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func urlQuery(q string) string {
	return strings.NewReplacer(" ", "%20", "\n", "%20").Replace(q)
}

func applySchema() error {
	raw, err := os.ReadFile(schemaPath)
	if err != nil {
		return err
	}
	for _, stmt := range strings.Split(strings.ReplaceAll(string(raw), "{db}", db), "\n;\n") {
		var lines []string
		for _, ln := range strings.Split(stmt, "\n") {
			if !strings.HasPrefix(strings.TrimSpace(ln), "--") {
				lines = append(lines, ln)
			}
		}
		if sql := strings.TrimSpace(strings.Join(lines, "\n")); sql != "" {
			if err := clickhouse(sql, nil); err != nil {
				return err
			}
		}
	}
	return nil
}

func insert(table string, rows []map[string]any) error {
	if len(rows) == 0 {
		return nil
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	for _, r := range rows {
		if err := enc.Encode(r); err != nil {
			return err
		}
	}
	return clickhouse(fmt.Sprintf("INSERT INTO %s.%s FORMAT JSONEachRow", db, table), buf.Bytes())
}

// --- value rendering (SPEC.md, "Mapping") ---------------------------------------

func attrs(m pcommon.Map) map[string]string {
	out := make(map[string]string, m.Len())
	m.Range(func(k string, v pcommon.Value) bool {
		out[k] = v.AsString()
		return true
	})
	return out
}

func ts(t pcommon.Timestamp) string {
	ns := uint64(t)
	return fmt.Sprintf("%d.%09d", ns/1e9, ns%1e9)
}

func traceID(id pcommon.TraceID) string {
	if id.IsEmpty() {
		return ""
	}
	return hex.EncodeToString(id[:])
}

func spanID(id pcommon.SpanID) string {
	if id.IsEmpty() {
		return ""
	}
	return hex.EncodeToString(id[:])
}

// --- signal -> rows -------------------------------------------------------------

func logRows(ld plog.Logs) []map[string]any {
	var rows []map[string]any
	for i := 0; i < ld.ResourceLogs().Len(); i++ {
		rl := ld.ResourceLogs().At(i)
		res := attrs(rl.Resource().Attributes())
		for j := 0; j < rl.ScopeLogs().Len(); j++ {
			sl := rl.ScopeLogs().At(j)
			for k := 0; k < sl.LogRecords().Len(); k++ {
				r := sl.LogRecords().At(k)
				t := r.Timestamp()
				if t == 0 {
					t = r.ObservedTimestamp()
				}
				rows = append(rows, map[string]any{
					"Timestamp":          ts(t),
					"TraceId":            traceID(r.TraceID()),
					"SpanId":             spanID(r.SpanID()),
					"TraceFlags":         uint8(r.Flags()),
					"SeverityText":       r.SeverityText(),
					"SeverityNumber":     int32(r.SeverityNumber()),
					"ServiceName":        res["service.name"],
					"Body":               r.Body().AsString(),
					"ResourceAttributes": res,
					"ScopeName":          sl.Scope().Name(),
					"ScopeVersion":       sl.Scope().Version(),
					"LogAttributes":      attrs(r.Attributes()),
					"EventName":          r.EventName(),
				})
			}
		}
	}
	return rows
}

func spanRows(td ptrace.Traces) []map[string]any {
	var rows []map[string]any
	for i := 0; i < td.ResourceSpans().Len(); i++ {
		rs := td.ResourceSpans().At(i)
		res := attrs(rs.Resource().Attributes())
		for j := 0; j < rs.ScopeSpans().Len(); j++ {
			ss := rs.ScopeSpans().At(j)
			for k := 0; k < ss.Spans().Len(); k++ {
				sp := ss.Spans().At(k)
				dur := uint64(0)
				if sp.EndTimestamp() > sp.StartTimestamp() {
					dur = uint64(sp.EndTimestamp() - sp.StartTimestamp())
				}
				rows = append(rows, map[string]any{
					"Timestamp":          ts(sp.StartTimestamp()),
					"TraceId":            traceID(sp.TraceID()),
					"SpanId":             spanID(sp.SpanID()),
					"ParentSpanId":       spanID(sp.ParentSpanID()),
					"TraceState":         sp.TraceState().AsRaw(),
					"SpanName":           sp.Name(),
					"SpanKind":           sp.Kind().String(),
					"ServiceName":        res["service.name"],
					"ResourceAttributes": res,
					"ScopeName":          ss.Scope().Name(),
					"ScopeVersion":       ss.Scope().Version(),
					"SpanAttributes":     attrs(sp.Attributes()),
					"Duration":           dur,
					"StatusCode":         sp.Status().Code().String(),
					"StatusMessage":      sp.Status().Message(),
				})
			}
		}
	}
	return rows
}

func pointValue(dp pmetric.NumberDataPoint) float64 {
	if dp.ValueType() == pmetric.NumberDataPointValueTypeInt {
		return float64(dp.IntValue())
	}
	return dp.DoubleValue()
}

func metricRows(md pmetric.Metrics) (sums, gauges []map[string]any, rejected int64, rejectedTypes []string) {
	seen := map[string]bool{}
	for i := 0; i < md.ResourceMetrics().Len(); i++ {
		rm := md.ResourceMetrics().At(i)
		res := attrs(rm.Resource().Attributes())
		for j := 0; j < rm.ScopeMetrics().Len(); j++ {
			sm := rm.ScopeMetrics().At(j)
			for k := 0; k < sm.Metrics().Len(); k++ {
				m := sm.Metrics().At(k)
				base := func(dp pmetric.NumberDataPoint) map[string]any {
					return map[string]any{
						"ResourceAttributes": res,
						"ServiceName":        res["service.name"],
						"ScopeName":          sm.Scope().Name(),
						"ScopeVersion":       sm.Scope().Version(),
						"MetricName":         m.Name(),
						"MetricDescription":  m.Description(),
						"MetricUnit":         m.Unit(),
						"Attributes":         attrs(dp.Attributes()),
						"StartTimeUnix":      ts(dp.StartTimestamp()),
						"TimeUnix":           ts(dp.Timestamp()),
						"Value":              pointValue(dp),
					}
				}
				switch m.Type() {
				case pmetric.MetricTypeSum:
					s := m.Sum()
					for p := 0; p < s.DataPoints().Len(); p++ {
						row := base(s.DataPoints().At(p))
						row["AggregationTemporality"] = int32(s.AggregationTemporality())
						row["IsMonotonic"] = s.IsMonotonic()
						sums = append(sums, row)
					}
				case pmetric.MetricTypeGauge:
					g := m.Gauge()
					for p := 0; p < g.DataPoints().Len(); p++ {
						gauges = append(gauges, base(g.DataPoints().At(p)))
					}
				case pmetric.MetricTypeHistogram:
					rejected += int64(m.Histogram().DataPoints().Len())
					seen["histogram"] = true
				case pmetric.MetricTypeExponentialHistogram:
					rejected += int64(m.ExponentialHistogram().DataPoints().Len())
					seen["exponentialHistogram"] = true
				case pmetric.MetricTypeSummary:
					rejected += int64(m.Summary().DataPoints().Len())
					seen["summary"] = true
				}
			}
		}
	}
	for t := range seen {
		rejectedTypes = append(rejectedTypes, t)
	}
	sort.Strings(rejectedTypes)
	return
}

// --- HTTP -------------------------------------------------------------------------

type codec interface {
	UnmarshalProto([]byte) error
	UnmarshalJSON([]byte) error
}

type encoder interface {
	MarshalProto() ([]byte, error)
	MarshalJSON() ([]byte, error)
}

func handle(signal string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if !ready.Load() {
			http.Error(w, "starting", http.StatusServiceUnavailable)
			return
		}
		ctype, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		isJSON := ctype == "application/json"
		if !isJSON && ctype != "application/x-protobuf" {
			http.Error(w, fmt.Sprintf("unsupported content type %q", ctype), http.StatusUnsupportedMediaType)
			return
		}
		var body io.Reader = r.Body
		if strings.EqualFold(r.Header.Get("Content-Encoding"), "gzip") {
			gz, err := gzip.NewReader(r.Body)
			if err != nil {
				http.Error(w, "cannot decode body: "+err.Error(), http.StatusBadRequest)
				return
			}
			defer gz.Close()
			body = gz
		}
		raw, err := io.ReadAll(body)
		if err != nil {
			http.Error(w, "cannot decode body: "+err.Error(), http.StatusBadRequest)
			return
		}
		decode := func(req codec) error {
			if len(bytes.TrimSpace(raw)) == 0 {
				return nil
			}
			if isJSON {
				return req.UnmarshalJSON(raw)
			}
			return req.UnmarshalProto(raw)
		}

		var resp encoder
		switch signal {
		case "logs":
			req := plogotlp.NewExportRequest()
			if err = decode(&req); err == nil {
				err = insert("otel_logs", logRows(req.Logs()))
				resp = plogotlp.NewExportResponse()
			}
		case "traces":
			req := ptraceotlp.NewExportRequest()
			if err = decode(&req); err == nil {
				err = insert("otel_traces", spanRows(req.Traces()))
				resp = ptraceotlp.NewExportResponse()
			}
		case "metrics":
			req := pmetricotlp.NewExportRequest()
			if err = decode(&req); err == nil {
				sums, gauges, rejected, types := metricRows(req.Metrics())
				if err = insert("otel_metrics_sum", sums); err == nil {
					err = insert("otel_metrics_gauge", gauges)
				}
				out := pmetricotlp.NewExportResponse()
				if rejected > 0 {
					out.PartialSuccess().SetRejectedDataPoints(rejected)
					out.PartialSuccess().SetErrorMessage("unsupported metric type(s): " + strings.Join(types, ", "))
				}
				resp = out
			}
		}
		if err != nil {
			if errors.Is(err, errIngest) {
				log.Print(err)
				http.Error(w, err.Error(), http.StatusServiceUnavailable)
			} else {
				http.Error(w, "cannot decode body: "+err.Error(), http.StatusBadRequest)
			}
			return
		}

		var out []byte
		if isJSON {
			out, err = resp.MarshalJSON()
		} else {
			out, err = resp.MarshalProto()
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", ctype)
		_, _ = w.Write(out)
	}
}

func main() {
	go func() {
		for {
			if err := applySchema(); err != nil {
				log.Printf("waiting for clickhouse: %v", err)
				time.Sleep(2 * time.Second)
				continue
			}
			ready.Store(true)
			log.Printf("schema applied to %s.*; ready", db)
			return
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/logs", handle("logs"))
	mux.HandleFunc("/v1/traces", handle("traces"))
	mux.HandleFunc("/v1/metrics", handle("metrics"))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		if ready.Load() {
			_, _ = w.Write([]byte("ok"))
			return
		}
		http.Error(w, "starting", http.StatusServiceUnavailable)
	})
	log.Printf("oteru-ingestor (go) listening on :%s -> %s.*", port, db)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
