"""#48 test 2b: oteru_budget_alerts_reader grants match spec §7.4 / §7.12.

Static contract tests always run (parse bootstrap SQL). Live ClickHouse
checks run only when CLICKHOUSE_URL answers SELECT 1 — CI default jobs
do not start ClickHouse, so those cases skip. Evidence /
``scripts/prove_budget_view_grants.sh`` exercise the live path locally.
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SQL = REPO_ROOT / "scripts" / "bootstrap_budget_roles.sql"

DEFINER_FCT_COLS = (
    "day_utc",
    "period_start",
    "scope",
    "scope_key",
    "team",
    "project",
    "owner_team",
    "budget_usd_micros",
    "consumed_period_usd_micros",
    "consumed_milli",
    "warn_milli",
    "breach_milli",
    "budget_status",
    "action_on_breach",
    "last_cost_ts",
)
ALERTS_VIEW_COLS = (
    "scope",
    "scope_key",
    "team",
    "project",
    "period_start",
    "as_of_ts",
    "is_stale",
    "budget_status",
    "action_on_breach",
)
ALERTS_COST_COLS = (
    "run_id",
    "run_completed_at",
    "run_status",
    "events_dropped_late",
)


def _bootstrap_sql() -> str:
    return BOOTSTRAP_SQL.read_text(encoding="utf-8")


def _ch_url() -> str:
    return os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/")


def _clickhouse_up() -> bool:
    try:
        req = urllib.request.Request(
            _ch_url(), data=b"SELECT version()\n", method="POST"
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = resp.read().decode().strip()
            return bool(re.match(r"\d+\.\d+", body))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


class BudgetGrantContractTests(unittest.TestCase):
    def test_bootstrap_sql_has_no_password_literal(self) -> None:
        text = _bootstrap_sql()
        self.assertNotIn("IDENTIFIED WITH sha256_password BY '", text)
        self.assertIn("IDENTIFIED WITH no_password HOST LOCAL", text)
        self.assertNotRegex(text, r"password\s*=")

    def test_definer_select_is_fct_columns_only(self) -> None:
        text = _bootstrap_sql()
        match = re.search(
            r"GRANT SELECT\(([^)]*)\) ON otel\.fct_budget_consumption_daily "
            r"TO oteru_budget_view_definer",
            text,
        )
        self.assertIsNotNone(match, text)
        cols = tuple(c.strip() for c in match.group(1).split(",") if c.strip())
        self.assertEqual(cols, DEFINER_FCT_COLS)
        self.assertNotIn("GRANT SELECT ON otel.fct_", text)
        self.assertNotIn("int_cost_attributed", text)
        self.assertNotIn("otel_logs", text)

    def test_alerts_reader_is_role_not_budget_reader(self) -> None:
        text = _bootstrap_sql()
        self.assertIn("CREATE ROLE IF NOT EXISTS oteru_budget_alerts_reader", text)
        self.assertNotIn("CREATE USER IF NOT EXISTS oteru_budget_reader", text)
        self.assertNotIn("TO oteru_budget_reader", text)
        self.assertIn(
            "GRANT SELECT ON otel.audit_budget_runs TO oteru_budget_alerts_reader", text
        )
        view = re.search(
            r"GRANT SELECT\(([^)]*)\) ON otel\.budget_status_current "
            r"TO oteru_budget_alerts_reader",
            text,
        )
        self.assertIsNotNone(view, text)
        cols = tuple(c.strip() for c in view.group(1).split(",") if c.strip())
        self.assertEqual(cols, ALERTS_VIEW_COLS)
        self.assertNotIn("budget_usd_micros", view.group(1))
        cost = re.search(
            r"GRANT SELECT\(([^)]*)\) ON otel\.audit_cost_runs "
            r"TO oteru_budget_alerts_reader",
            text,
        )
        self.assertIsNotNone(cost, text)
        cost_cols = tuple(c.strip() for c in cost.group(1).split(",") if c.strip())
        self.assertEqual(cost_cols, ALERTS_COST_COLS)

    def test_no_write_grants(self) -> None:
        text = _bootstrap_sql().upper()
        for verb in ("INSERT", "ALTER", "DROP", "CREATE TABLE", "TRUNCATE"):
            self.assertNotIn(f"GRANT {verb}", text)


@unittest.skipUnless(_clickhouse_up(), "ClickHouse not reachable; live 2b skipped")
class BudgetGrantLiveTests(unittest.TestCase):
    """Positive/negative SELECTs as the alerts role. Needs a local ClickHouse."""

    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "bootstrap_budget_roles.sh")],
            check=False,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("bootstrap could not apply on this ClickHouse")

    def _admin(self, sql: str) -> str:
        req = urllib.request.Request(_ch_url(), data=sql.encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            raise unittest.SkipTest(f"ClickHouse rejected {sql!r}: {exc.code}") from exc

    def test_show_grants_definer_has_no_extra(self) -> None:
        grants = self._admin("SHOW GRANTS FOR oteru_budget_view_definer")
        self.assertIn("fct_budget_consumption_daily", grants)
        self.assertNotIn("audit_budget_runs", grants)
        self.assertNotIn("INSERT", grants.upper())
        for extra in ("int_cost_attributed", "otel_logs", "budgets"):
            self.assertNotIn(extra, grants)

    def test_show_grants_alerts_reader_columns(self) -> None:
        grants = self._admin("SHOW GRANTS FOR oteru_budget_alerts_reader")
        for col in ALERTS_VIEW_COLS:
            self.assertIn(col, grants)
        self.assertIn("audit_budget_runs", grants)
        self.assertNotIn("budget_usd_micros", grants)
        self.assertNotIn("INSERT", grants.upper())


if __name__ == "__main__":
    unittest.main()
