from datetime import UTC, datetime

import pytest
from oteru_drift.journey import Artifact, ArtifactLink, reconstruct_journey


def test_journey_exposes_missing_stages_and_unclassified_links() -> None:
    now = datetime.now(UTC)
    artifacts = [Artifact("s", "session", "1", now, "trace:s")]
    result = reconstruct_journey(
        artifacts, [ArtifactLink("s", "missing", "unclassified")]
    )
    assert "missing:pull_request" in result["gaps"]
    assert "unclassified:s->missing" in result["gaps"]


def test_inference_cannot_assert_authorship() -> None:
    with pytest.raises(ValueError, match="authorship"):
        ArtifactLink("a", "b", "inferred", actor_id="person")
