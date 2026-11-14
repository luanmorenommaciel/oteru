"""Stdlib tests for scripts/ephemeral_ch_guard.py.

No ClickHouse needed: urlopen is stubbed and must never be reached when the
guard refuses.
"""

from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ephemeral_ch_guard as guard

SECRET_PW = "pw-should-never-print"
OK_LOCAL = {
    "OTERU_EPHEMERAL_CH": "1",
    "CLICKHOUSE_HOST": "127.0.0.1",
    "CLICKHOUSE_PORT": "38123",
}

REFUSED = {
    "no ephemeral flag": {"CLICKHOUSE_HOST": "127.0.0.1", "CLICKHOUSE_PORT": "38123"},
    "flag not 1": {**OK_LOCAL, "OTERU_EPHEMERAL_CH": "true"},
    "no host": {"OTERU_EPHEMERAL_CH": "1", "CLICKHOUSE_PORT": "38123"},
    "remote host": {**OK_LOCAL, "CLICKHOUSE_HOST": "prod.example.com"},
    "clickhouse outside CI": {**OK_LOCAL, "CLICKHOUSE_HOST": "clickhouse"},
    "clickhouse GITHUB_ACTIONS=false": {
        **OK_LOCAL,
        "CLICKHOUSE_HOST": "clickhouse",
        "GITHUB_ACTIONS": "false",
    },
    "no port": {"OTERU_EPHEMERAL_CH": "1", "CLICKHOUSE_HOST": "127.0.0.1"},
    "no port in CI": {
        "OTERU_EPHEMERAL_CH": "1",
        "CLICKHOUSE_HOST": "localhost",
        "GITHUB_ACTIONS": "true",
    },
    "non-numeric port": {**OK_LOCAL, "CLICKHOUSE_PORT": "8123x"},
    "local 8123 outside CI": {**OK_LOCAL, "CLICKHOUSE_PORT": "8123"},
    "local 8123 GITHUB_ACTIONS=false": {
        **OK_LOCAL,
        "CLICKHOUSE_PORT": "8123",
        "GITHUB_ACTIONS": "false",
    },
}
ALLOWED = {
    "127.0.0.1": OK_LOCAL,
    "localhost": {**OK_LOCAL, "CLICKHOUSE_HOST": "localhost"},
    "::1": {**OK_LOCAL, "CLICKHOUSE_HOST": "::1"},
    "clickhouse in CI": {
        **OK_LOCAL,
        "CLICKHOUSE_HOST": "clickhouse",
        "GITHUB_ACTIONS": "true",
    },
    "CI localhost:8123": {
        **OK_LOCAL,
        "CLICKHOUSE_HOST": "localhost",
        "CLICKHOUSE_PORT": "8123",
        "GITHUB_ACTIONS": "true",
    },
}


class GuardTest(unittest.TestCase):
    def test_refused(self):
        for name, env in REFUSED.items():
            with self.subTest(name):
                self.assertIsNotNone(guard.check(env))

    def test_allowed(self):
        for name, env in ALLOWED.items():
            with self.subTest(name):
                self.assertIsNone(guard.check(env))

    def test_url_uses_env_only(self):
        self.assertEqual(guard.url(OK_LOCAL), "http://127.0.0.1:38123/")
        self.assertEqual(
            guard.url({**OK_LOCAL, "CLICKHOUSE_HOST": "::1"}), "http://[::1]:38123/"
        )

    def _assert_refused_before_io(self, fn, env):
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch("urllib.request.urlopen") as urlopen,
            redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as cm,
        ):
            fn()
        self.assertEqual(cm.exception.code, 2)
        urlopen.assert_not_called()
        self.assertIn("REFUSED", err.getvalue())

    def test_require_and_post_refuse_before_io(self):
        secret_pw = "pw-should-never-print"
        for name, env in REFUSED.items():
            env = {**env, "CLICKHOUSE_PASSWORD": secret_pw}
            for fn in (guard.require, lambda: guard.post("SELECT 1")):
                with self.subTest(name=name):
                    self._assert_refused_before_io(fn, env)

    def test_refusal_never_prints_credentials(self):
        env = {**REFUSED["remote host"], "CLICKHOUSE_PASSWORD": "pw-should-never-print"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit),
        ):
            guard.require()
        self.assertNotIn("pw-should-never-print", err.getvalue())

    @staticmethod
    def _resp(body: bytes):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        return resp

    def test_allowed_post_checks_marker_then_sends(self):
        env = {**OK_LOCAL, "CLICKHOUSE_USER": "u", "CLICKHOUSE_PASSWORD": SECRET_PW}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch(
                "urllib.request.urlopen",
                side_effect=[self._resp(b"1\n"), self._resp(b"42\n")],
            ) as urlopen,
        ):
            self.assertEqual(guard.post("SELECT 42"), "42\n")
        self.assertEqual(urlopen.call_count, 2)
        first, second = (c.args[0] for c in urlopen.call_args_list)
        self.assertEqual(first.data, b"EXISTS TABLE default.oteru_ephemeral_marker")
        self.assertEqual(second.data, b"SELECT 42")
        for req in (first, second):
            self.assertEqual(req.full_url, "http://127.0.0.1:38123/")
            self.assertNotIn(SECRET_PW, req.full_url)
            self.assertEqual(req.get_header("X-clickhouse-user"), "u")
            self.assertEqual(req.get_header("X-clickhouse-key"), SECRET_PW)

    def test_require_marker_present_ok(self):
        env = {**OK_LOCAL, "CLICKHOUSE_PASSWORD": SECRET_PW}
        with mock.patch("urllib.request.urlopen", return_value=self._resp(b"1\n")):
            guard.require_marker(env)

    def _assert_marker_refused(self, urlopen_kwargs):
        env = {**OK_LOCAL, "CLICKHOUSE_PASSWORD": SECRET_PW}
        with (
            mock.patch("urllib.request.urlopen", **urlopen_kwargs) as urlopen,
            redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as cm,
        ):
            guard.post("INSERT INTO t VALUES (1)", env)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(urlopen.call_count, 1, "no write after a failed marker check")
        self.assertIn("REFUSED", err.getvalue())
        self.assertNotIn(SECRET_PW, err.getvalue())
        return err.getvalue()

    def test_marker_absent_refused(self):
        msg = self._assert_marker_refused({"return_value": self._resp(b"0\n")})
        self.assertIn("oteru_ephemeral_marker is missing", msg)

    def test_marker_http_error_refused(self):
        exc = urllib.error.HTTPError(
            "http://127.0.0.1:38123/", 516, f"auth failed {SECRET_PW}", {}, None
        )
        msg = self._assert_marker_refused({"side_effect": exc})
        self.assertIn("HTTP 516", msg)

    def test_marker_connection_error_refused(self):
        exc = urllib.error.URLError(f"refused {SECRET_PW}")
        msg = self._assert_marker_refused({"side_effect": exc})
        self.assertIn("URLError", msg)

    def test_marker_check_skipped_when_host_refused(self):
        env = {**REFUSED["local 8123 outside CI"], "CLICKHOUSE_PASSWORD": SECRET_PW}
        with (
            mock.patch("urllib.request.urlopen") as urlopen,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            guard.require_marker(env)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
