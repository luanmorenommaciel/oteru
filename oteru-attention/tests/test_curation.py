from __future__ import annotations

import sqlite3

from oteru_attention.curation import QuestionLedger


def test_repeated_intent_requires_review_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "state" / "curation.sqlite3"
    ledger = QuestionLedger(path)
    assert ledger.promote("failures") is False
    for count in range(1, 4):
        assert ledger.record("failures") == count
    assert ledger.overview()["candidates"][0]["intent"] == "failures"
    assert ledger.promote("failures") is True

    restarted = QuestionLedger(path)
    assert restarted.overview()["promoted"][0]["asks"] == 3
    assert restarted.remove("failures") is True
    assert restarted.overview()["candidates"][0]["intent"] == "failures"
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(question_intents)")]
    assert columns == ["intent", "asks", "first_seen", "last_seen"]
