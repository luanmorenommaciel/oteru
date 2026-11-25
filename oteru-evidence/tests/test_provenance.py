from oteru_evidence.provenance import LinkRecord, ProvenanceKind


def test_link_preserves_declared_then_confirmed_history() -> None:
    link = LinkRecord("link-1", "session:s1", "pr:10")
    link.transition(ProvenanceKind.DECLARED, actor="agent:a", evidence_ref="event:1")
    link.transition(ProvenanceKind.CONFIRMED, actor="human:h", evidence_ref="review:2")
    assert link.current is ProvenanceKind.CONFIRMED
    assert [item.current for item in link.history] == [
        ProvenanceKind.DECLARED,
        ProvenanceKind.CONFIRMED,
    ]
    assert link.history[0].actor == "agent:a"


def test_confirmed_link_cannot_be_downgraded() -> None:
    link = LinkRecord("link", "a", "b")
    link.transition(ProvenanceKind.CONFIRMED, actor="human", evidence_ref="review")
    try:
        link.transition(ProvenanceKind.INFERRED, actor="rule", evidence_ref="rule-run")
    except ValueError as exc:
        assert "invalid provenance transition" in str(exc)
    else:
        raise AssertionError("confirmed provenance was overwritten")
