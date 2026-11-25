import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from benchmarks.ingestor.benchmark import benchmark, percentile


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(202)
        self.end_headers()

    def log_message(self, *_args):
        pass


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.99) == 4


def test_benchmark_records_success_and_latency() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = benchmark(
            f"http://127.0.0.1:{server.server_port}/v1/logs", b"{}", 12, 3, 2
        )
    finally:
        server.shutdown()
        thread.join()
    assert result["successes"] == 12
    assert result["errors"] == 0
    assert result["throughput_rps"] > 0
    assert result["latency_ms"]["p95"] >= 0
