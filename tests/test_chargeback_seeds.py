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

    def test_hmac_regexes_are_exact(self) -> None:
        self.assertEqual(self.guard.HMAC_RE.pattern, r"^hmac:v1:[0-9a-f]{64}$")
        self.assertEqual(
            self.guard.HMAC_FIXTURE_RE.pattern, r"^hmac:v[0-9]+:[0-9a-f]{64}$"
        )
        self.assertEqual(
            self.guard.HMAC_ZERO_DIGEST_RE.pattern, r"^hmac:v[0-9]+:0{64}$"
        )

    def test_all_zero_digest_in_production_fails(self) -> None:
        """Positive: hmac:v1: + 64 zeros outside fixtures is rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds"
            dest.mkdir()
            zero = "hmac:v1:" + "0" * 64
            (dest / "chargeback_actor_map.csv").write_text(
                f"actor_id,team,project\n{zero},platform,oteru\n",
                encoding="utf-8",
            )
            violations = self.guard.check_tree(root)
            self.assertTrue(
                any("all-zero digest" in v for v in violations),
                msg=violations,
            )
            self.assertEqual(self.guard.main(["--path", str(root)]), 1)

    def test_nonzero_digest_outside_fixtures_passes(self) -> None:
        """Negative: a non-all-zero hmac:v1: digest outside fixtures still passes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds"
            dest.mkdir()
            near_zero = "hmac:v1:" + "0" * 63 + "1"
            (dest / "chargeback_actor_map.csv").write_text(
                f"actor_id,team,project\n{near_zero},platform,oteru\n",
                encoding="utf-8",
            )
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_all_zero_digest_in_fixtures_allowed(self) -> None:
        """Fixture path: hmac:v1:/hmac:v2: + 64 zeros under chargeback_fixtures/ pass."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds" / "chargeback_fixtures"
            dest.mkdir(parents=True)
            zero_v1 = "hmac:v1:" + "0" * 64
            zero_v2 = "hmac:v2:" + "0" * 64
            (dest / "chargeback_actor_map_fixture.csv").write_text(
                f"actor_id,team,project\n{zero_v1},time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            (dest / "chargeback_actor_map_fixture_v2.csv").write_text(
                f"actor_id,team,project\n{zero_v1},time-fixture,projeto-fixture\n"
                f"{zero_v2},time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

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

    def test_scans_chargeback_fixtures_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "oteru-dbt" / "seeds"
            fixtures = seeds / "chargeback_fixtures"
            fixtures.mkdir(parents=True)
            (seeds / "chargeback_actor_map.csv").write_text(
                "actor_id,team,project\n", encoding="utf-8"
            )
            (seeds / "chargeback_service_map.csv").write_text(
                "service_name,team,project,owner_team,valid_from\n"
                "claude-code,plataforma,oteru,plataforma,2026-10-21\n",
                encoding="utf-8",
            )
            (fixtures / "chargeback_actor_map_fixture.csv").write_text(
                f"actor_id,team,project\n{DUMMY_HMAC},time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            v2 = "hmac:v2:" + "0" * 64
            (fixtures / "chargeback_actor_map_fixture_v2.csv").write_text(
                f"actor_id,team,project\n{DUMMY_HMAC},time-fixture,projeto-fixture\n"
                f"{v2},time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            found = {
                str(p.relative_to(root)) for p in self.guard.iter_chargeback_seeds(root)
            }
            self.assertIn("oteru-dbt/seeds/chargeback_actor_map.csv", found)
            self.assertIn(
                "oteru-dbt/seeds/chargeback_fixtures/chargeback_actor_map_fixture.csv",
                found,
            )
            self.assertIn(
                "oteru-dbt/seeds/chargeback_fixtures/chargeback_actor_map_fixture_v2.csv",
                found,
            )
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_at_in_chargeback_fixtures_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds" / "chargeback_fixtures"
            dest.mkdir(parents=True)
            shutil.copy(
                FIXTURES / "at_forbidden.csv", dest / "chargeback_actor_map_fixture.csv"
            )
            violations = self.guard.check_tree(root)
            self.assertTrue(
                any("'@' is forbidden" in v for v in violations), msg=violations
            )
            self.assertEqual(self.guard.main(["--path", str(root)]), 1)

    def test_v2_hmac_fails_in_production_actor_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds"
            dest.mkdir()
            v2 = "hmac:v2:" + "0" * 64
            (dest / "chargeback_actor_map.csv").write_text(
                f"actor_id,team,project\n{v2},platform,oteru\n",
                encoding="utf-8",
            )
            violations = self.guard.check_tree(root)
            self.assertTrue(
                any(
                    "does not match" in v and self.guard.HMAC_RE.pattern in v
                    for v in violations
                ),
                msg=violations,
            )
            self.assertEqual(self.guard.main(["--path", str(root)]), 1)

    def test_v2_hmac_in_fixtures_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds" / "chargeback_fixtures"
            dest.mkdir(parents=True)
            v2 = "hmac:v2:" + "0" * 64
            (dest / "chargeback_actor_map_fixture_v2.csv").write_text(
                f"actor_id,team,project\n{v2},time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_malformed_hmac_in_fixtures_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "seeds" / "chargeback_fixtures"
            dest.mkdir(parents=True)
            (dest / "chargeback_actor_map_fixture.csv").write_text(
                "actor_id,team,project\nhmac:v2:deadbeef,time-fixture,projeto-fixture\n",
                encoding="utf-8",
            )
            violations = self.guard.check_tree(root)
            self.assertTrue(
                any(
                    "does not match" in v and self.guard.HMAC_FIXTURE_RE.pattern in v
                    for v in violations
                ),
                msg=violations,
            )
            self.assertEqual(self.guard.main(["--path", str(root)]), 1)

    def _git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "dev@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "dev"],
            check=True,
            capture_output=True,
        )

    def test_untracked_and_ignored_files_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "seeds"
            leaked = root / "oteru-dbt" / "target" / "seeds"
            seeds.mkdir()
            leaked.mkdir(parents=True)
            (seeds / "chargeback_actor_map.csv").write_text(
                "actor_id,team,project\n", encoding="utf-8"
            )
            (root / ".gitignore").write_text("oteru-dbt/target/\n", encoding="utf-8")
            self._git_repo(root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    "seeds/chargeback_actor_map.csv",
                    ".gitignore",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", "tracked seed"],
                check=True,
                capture_output=True,
            )
            junk = "actor_id,team,project\n" + "user@" + "example.invalid,x,y\n"
            (leaked / "chargeback_leaked.csv").write_text(junk, encoding="utf-8")
            (seeds / "chargeback_untracked.csv").write_text(junk, encoding="utf-8")
            found = {
                str(p.relative_to(root)) for p in self.guard.iter_chargeback_seeds(root)
            }
            self.assertEqual(found, {"seeds/chargeback_actor_map.csv"})
            self.assertEqual(self.guard.check_tree(root), [])
            self.assertEqual(self.guard.main(["--path", str(root)]), 0)

    def test_non_csv_tracked_file_is_reported_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "seeds"
            seeds.mkdir()
            (seeds / "chargeback_notes.md").write_text(
                "not a csv seed\n", encoding="utf-8"
            )
            self._git_repo(root)
            subprocess.run(
                ["git", "-C", str(root), "add", "seeds/chargeback_notes.md"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", "non-csv"],
                check=True,
                capture_output=True,
            )
            try:
                violations = self.guard.check_tree(root)
            except AttributeError as exc:  # noqa: BLE001 — must not leak
                self.fail(f"AttributeError leaked: {exc}")
            self.assertTrue(
                any("must be a .csv file" in v for v in violations),
                msg=violations,
            )
            self.assertEqual(self.guard.main(["--path", str(root)]), 1)

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
