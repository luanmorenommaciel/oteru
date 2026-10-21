#!/usr/bin/env python3
"""#48 S1: carrega a fixture de custo com o ÚLTIMO evento em T = agora - N horas.

Reusa a leitura e o pseudônimo de teste de load_shifted_cost_fixture.py (não editado)
e desloca todos os timestamps para o passado, preservando os intervalos entre eventos.
Só para ClickHouse LOCAL descartável. Imprime T (UTC) na saída.

Uso: python3 oteru-dbt/tests/load_budget_stale_fixture.py <clickhouse_http_url> [horas=28]
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("base_loader", HERE / "load_shifted_cost_fixture.py")
base = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(base)


def main() -> None:
    url = sys.argv[1]
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 28
    recs = base.records()
    target = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0)
    delta = target - max(r["ts"] for r in recs)
    for r in recs:
        ts = r["ts"] + delta
        ns = int(ts.timestamp()) * 10**9 + ts.microsecond * 1000
        q = (
            "INSERT INTO otel.otel_logs (Timestamp, Body, ServiceName, ResourceAttributes, LogAttributes) VALUES "
            f"(fromUnixTimestamp64Nano({ns}), '{r['body']}', '{r['svc']}', "
            f"{base.esc_map(r['res'])}, {base.esc_map(r['log'])})"
        )
        urllib.request.urlopen(urllib.request.Request(url, data=q.encode())).read()
    print(f"loaded {len(recs)} records; T (último event_ts, UTC) = {target:%Y-%m-%d %H:%M:%S}; idade = {hours}h")


if __name__ == "__main__":
    main()
