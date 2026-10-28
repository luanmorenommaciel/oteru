"""#48 test 2b: oteru_budget_alerts_reader grants match spec §7.4 / §7.12.

Static contract tests always run (parse bootstrap SQL). Live ClickHouse
checks run only when CLICKHOUSE_URL answers SELECT 1 — CI default jobs
do not start ClickHouse, so those cases skip. Evidence /
``scripts/prove_budget_view_grants.sh`` exercise the live path locally.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SQL = REPO_ROOT / "scripts" / "bootstrap_budget_roles.sql"
BOOTSTRAP_SH = REPO_ROOT / "scripts" / "bootstrap_budget_roles.sh"
BOOTSTRAP_HELPER = REPO_ROOT / "scripts" / "bootstrap_budget_sql.py"
EXPECTED_STATEMENT_COUNT = 6

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


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_budget_sql", BOOTSTRAP_HELPER
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ch_url() -> str:
    return os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/")


def _ch_headers() -> dict[str, str]:
    headers = {"X-ClickHouse-User": os.environ.get("CLICKHOUSE_USER", "default")}
    password = os.environ.get("CLICKHOUSE_PASSWORD", "")
    if password:
        headers["X-ClickHouse-Key"] = password
    return headers


def _clickhouse_up() -> bool:
    try:
        req = urllib.request.Request(
            _ch_url(),
            data=b"SELECT version()\n",
            method="POST",
            headers=_ch_headers(),
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

    def test_split_committed_sql_is_single_statements(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(_bootstrap_sql())
        self.assertEqual(len(statements), EXPECTED_STATEMENT_COUNT)
        self.assertEqual(helper.single_statement_errors(statements), [])
        for stmt in statements:
            self.assertNotIn(";", stmt)
            self.assertEqual(helper.command_count(stmt), 1)

    def test_syntax_rejects_unsplit_file(self) -> None:
        helper = _load_helper()
        errors = helper.single_statement_errors([_bootstrap_sql()])
        self.assertTrue(errors)
        self.assertTrue(
            any("multi-statements are not allowed" in err for err in errors),
            msg=errors,
        )

    def test_split_semicolon_inside_single_quoted_string(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "CREATE USER IF NOT EXISTS x "
            "IDENTIFIED WITH sha256_password BY 'ab;cd' HOST LOCAL;\n"
            "CREATE ROLE IF NOT EXISTS y;\n"
        )
        self.assertEqual(len(statements), 2)
        self.assertIn("'ab;cd'", statements[0])
        self.assertEqual(statements[1], "CREATE ROLE IF NOT EXISTS y")

    def test_split_semicolon_inside_escaped_quotes(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "CREATE USER IF NOT EXISTS x "
            "IDENTIFIED WITH sha256_password BY 'it''s;secret' HOST LOCAL;\n"
            "CREATE ROLE IF NOT EXISTS y;\n"
        )
        self.assertEqual(len(statements), 2)
        self.assertIn("'it''s;secret'", statements[0])
        self.assertEqual(helper.command_count(statements[0]), 1)

    def test_split_semicolon_inside_line_comment(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "-- GRANT decoy; CREATE ROLE nope;\nCREATE ROLE IF NOT EXISTS y;\n"
        )
        self.assertEqual(statements, ["CREATE ROLE IF NOT EXISTS y"])

    def test_split_semicolon_inside_block_comment(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "/* GRANT decoy; CREATE ROLE nope; */\nCREATE ROLE IF NOT EXISTS y;\n"
        )
        self.assertEqual(statements, ["CREATE ROLE IF NOT EXISTS y"])

    def test_split_drops_blank_pieces(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "CREATE ROLE IF NOT EXISTS a;;;\n  ;\nCREATE ROLE IF NOT EXISTS b;\n"
        )
        self.assertEqual(
            statements,
            ["CREATE ROLE IF NOT EXISTS a", "CREATE ROLE IF NOT EXISTS b"],
        )

    def test_split_keeps_trailing_statement_without_semicolon(self) -> None:
        helper = _load_helper()
        statements = helper.split_statements(
            "CREATE ROLE IF NOT EXISTS a;\nCREATE ROLE IF NOT EXISTS b\n"
        )
        self.assertEqual(
            statements,
            ["CREATE ROLE IF NOT EXISTS a", "CREATE ROLE IF NOT EXISTS b"],
        )

    def test_redact_hides_password_and_url_userinfo(self) -> None:
        helper = _load_helper()
        hidden = helper.redact(
            "http://user:s3cret@127.0.0.1:8123/ and s3cret",
            ["s3cret"],
        )
        self.assertNotIn("s3cret", hidden)
        self.assertIn("http://***@127.0.0.1:8123/", hidden)

    def test_syntax_flag_checks_split(self) -> None:
        result = subprocess.run(
            ["bash", str(BOOTSTRAP_SH), "--syntax"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{EXPECTED_STATEMENT_COUNT} statements", result.stdout)
        self.assertIn("syntax OK", result.stdout)
        script = BOOTSTRAP_SH.read_text(encoding="utf-8")
        helper = BOOTSTRAP_HELPER.read_text(encoding="utf-8")
        self.assertIn("--syntax", script)
        self.assertIn("bootstrap_budget_sql.py", script)
        self.assertIn("--apply", script)
        self.assertNotIn("sys.stdout.write(sql)", script)
        self.assertNotIn("command -v clickhouse", script)
        self.assertNotIn("--apply-native", helper)
        self.assertNotIn("--multiquery", helper)

    def test_prove_script_sends_one_statement_per_request(self) -> None:
        prove = (REPO_ROOT / "scripts" / "prove_budget_view_grants.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('--data-binary "$1"', prove)
        self.assertNotIn("--data-binary @", prove)


@unittest.skipUnless(_clickhouse_up(), "ClickHouse not reachable; live 2b skipped")
class BudgetGrantLiveTests(unittest.TestCase):
    """Positive/negative SELECTs as the alerts role. Needs a local ClickHouse."""

    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            ["bash", str(BOOTSTRAP_SH), "--require"],
            check=False,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "bootstrap --require failed on a reachable ClickHouse: "
                f"{result.stderr or result.stdout}"
            )

    def _admin(self, sql: str) -> str:
        req = urllib.request.Request(
            _ch_url(), data=sql.encode(), method="POST", headers=_ch_headers()
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise AssertionError(
                f"ClickHouse rejected query ({exc.code}): {body}"
            ) from exc

    def test_http_rejects_whole_file_as_one_request(self) -> None:
        req = urllib.request.Request(
            _ch_url(),
            data=_bootstrap_sql().encode(),
            method="POST",
            headers=_ch_headers(),
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 400)
        body = caught.exception.read().decode("utf-8", "replace")
        self.assertIn("62", body)
        self.assertRegex(body, r"[Mm]ulti-statements are not allowed")

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

    def test_bootstrap_twice_is_idempotent_and_roles_exist(self) -> None:
        """HTTP apply twice; roles and grants stay in place."""

        def run_bootstrap() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", str(BOOTSTRAP_SH), "--require"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        first = run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("bootstrap_budget_roles: OK", first.stdout)
        users = self._admin(
            "SELECT name FROM system.users "
            "WHERE name = 'oteru_budget_view_definer' FORMAT TSV"
        )
        roles = self._admin(
            "SELECT name FROM system.roles "
            "WHERE name = 'oteru_budget_alerts_reader' FORMAT TSV"
        )
        self.assertEqual(users.strip(), "oteru_budget_view_definer")
        self.assertEqual(roles.strip(), "oteru_budget_alerts_reader")
        grants_definer = self._admin("SHOW GRANTS FOR oteru_budget_view_definer")
        grants_alerts = self._admin("SHOW GRANTS FOR oteru_budget_alerts_reader")
        self.assertIn("fct_budget_consumption_daily", grants_definer)
        self.assertIn("audit_budget_runs", grants_alerts)
        self.assertIn("budget_status_current", grants_alerts)
        self.assertIn("audit_cost_runs", grants_alerts)

        second = run_bootstrap()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("bootstrap_budget_roles: OK", second.stdout)
        self.assertEqual(
            self._admin("SHOW GRANTS FOR oteru_budget_view_definer"),
            grants_definer,
        )
        self.assertEqual(
            self._admin("SHOW GRANTS FOR oteru_budget_alerts_reader"),
            grants_alerts,
        )


if __name__ == "__main__":
    unittest.main()
