"""Local paths and policy loader shared by Collector contract tests."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("privacy_policy", ROOT / "privacy" / "compile.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def default():
    return json.loads((ROOT / "privacy" / "policy.json").read_text())
