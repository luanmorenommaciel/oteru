import json
from pathlib import Path

from scripts.check_compliance_pack import validate

SAMPLE = Path(__file__).parents[2] / "docs" / "compliance" / "sample-pack.json"


def test_sample_pack_satisfies_contract() -> None:
    assert validate(json.loads(SAMPLE.read_text(encoding="utf-8"))) == []


def test_positive_claim_requires_known_evidence() -> None:
    pack = json.loads(SAMPLE.read_text(encoding="utf-8"))
    pack["requirements"][0]["evidence_refs"] = ["missing.json"]
    errors = validate(pack)
    assert any("unknown artifacts" in error for error in errors)
