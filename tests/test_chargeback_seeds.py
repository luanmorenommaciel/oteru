"""Chargeback seed guard (#46 task 3): '@' banned; actor keys hmac:v1.

Fixtures live in tests/fixtures/chargeback_guard/ (not under seeds/chargeback_*,
except when a test copies them there). The RFC 2606 .invalid address lives
only in at_forbidden.csv — exact path documented in .gitleaks.toml and that
fixture's header.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "chargeback_guard"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"
DUMMY_HMAC = "hmac:v1:" + "a" * 64  # documented dummy; not an HMAC of any identifier


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "check_chargeback_seeds", REPO_ROOT / "scripts" / "check_chargeback_seeds.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gitleaks_bin() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    fallback = Path("/tmp/ocb-tools/gitleaks")
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    return None


class ChargebackSeedGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = _load_guard()

    def _scan_copied(
        self, fixture_name: str, seed_name: str = "chargeback_actor_map.csv"
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest_dir = root / "seeds"
            dest_dir.mkdir()
            shutil.copy(FIXTURES / fixture_name, dest_dir / seed_name)
            return self.guard.check_tree(root), self.guard.main(["--path", str(root)])

    def test_at_in_seed_value_fails(self) -> None:
        violations, rc = self._scan_copied("at_forbidden.csv")
        self.assertEqual(rc, 1)
        self.assertTrue(
            any("'@' is forbidden" in v for v in violations),
            msg=violations,
        )

    def test_valid_hmac_v1_key_passes(self) -> None:
        violations, rc = self._scan_copied("valid_hmac.csv")
        self.assertEqual(violations, [])
        self.assertEqual(rc, 0)
        self.assertIn(
            DUMMY_HMAC, (FIXTURES / "valid_hmac.csv").read_text(encoding="utf-8")
        )

    def test_malformed_hmac_fails(self) -> None:
        violations, rc = self._scan_copied("malformed_hmac.csv")
        self.assertEqual(rc, 1)
        self.assertTrue(
            any("does not match" in v and "hmac:v1:" in v for v in violations),
            msg=violations,
        )

    def test_header_only_actor_map_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds"
            dest.mkdir()
            (dest / "chargeback_actor_map.csv").write_text(
                "actor_id,team,project\n", encoding="utf-8"
            )
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_empty_glob_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "seeds").mkdir()
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_repo_scan_passes(self) -> None:
        self.assertEqual(self.guard.check_tree(REPO_ROOT), [])
        self.assertEqual(self.guard.main(["--path", str(REPO_ROOT)]), 0)

    def test_fake_email_only_in_documented_fixture(self) -> None:
        marker = "user@" + "example.invalid"
        hits: list[str] = []
        skip_dirs = {
            ".git",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            "site-packages",
        }
        allowed = {
            (FIXTURES / "at_forbidden.csv").resolve(),
            (FIXTURES / "README.md").resolve(),
        }
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for name in filenames:
                path = Path(dirpath) / name
                if path.resolve() in allowed:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if marker in text:
                    hits.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            hits, [], msg="fake email leaked outside the documented fixture"
        )


@unittest.skipUnless(_gitleaks_bin(), "gitleaks CLI not on PATH")
class ChargebackGitleaksRuleTests(unittest.TestCase):
    """Positive blocked / negative passes for rule chargeback-seed-at."""

    def _detect(self, seed_body: str) -> subprocess.CompletedProcess[str]:
        assert GITLEAKS_CONFIG.is_file()
        bin_path = _gitleaks_bin()
        assert bin_path is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "seeds").mkdir()
            (root / "seeds" / "chargeback_actor_map.csv").write_text(
                seed_body, encoding="utf-8"
            )
            return subprocess.run(
                [
                    bin_path,
                    "detect",
                    "--source",
                    str(root),
                    "--no-git",
                    "--no-banner",
                    "--verbose",
                    "--redact",
                    "--config",
                    str(GITLEAKS_CONFIG),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_gitleaks_blocks_at_in_chargeback_seed(self) -> None:
        body = (FIXTURES / "at_forbidden.csv").read_text(encoding="utf-8")
        result = self._detect(body)
        self.assertNotEqual(result.returncode, 0, msg=result.stderr + result.stdout)
        combined = result.stdout + result.stderr
        self.assertIn("chargeback-seed-at", combined)

    def test_gitleaks_passes_valid_hmac_seed(self) -> None:
        body = (FIXTURES / "valid_hmac.csv").read_text(encoding="utf-8")
        result = self._detect(body)
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
