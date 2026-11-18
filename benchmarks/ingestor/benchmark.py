#!/usr/bin/env python3
"""Repeatable HTTP benchmark harness for the Python, Go and Rust ingestors."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def post(url: str, payload: bytes, timeout: float) -> tuple[float, int]:
    start = time.perf_counter()
    request = Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        status = 0
    return (time.perf_counter() - start) * 1000, status


def benchmark(
    url: str, payload: bytes, requests: int, concurrency: int, timeout: float
) -> dict:
    started = time.perf_counter()
    samples: list[tuple[float, int]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(post, url, payload, timeout) for _ in range(requests)]
        for future in as_completed(futures):
            samples.append(future.result())
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in samples]
    successes = sum(1 for _, status in samples if 200 <= status < 300)
    return {
        "requests": requests,
        "successes": successes,
        "errors": requests - successes,
        "elapsed_seconds": elapsed,
        "throughput_rps": successes / elapsed if elapsed else 0,
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else math.nan,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target", action="append", required=True, help="name=http://host/path"
    )
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = args.payload.read_bytes()
    results = {}
    for target in args.target:
        name, url = target.split("=", 1)
        results[name] = benchmark(
            url, payload, args.requests, args.concurrency, args.timeout
        )
    args.output.write_text(
        json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0 if all(item["errors"] == 0 for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
