from htrflow_reconciler.guards import check_drift
from htrflow_reconciler.models import PipelineSpec
from htrflow_reconciler.parse import canonical_sha256

P = PipelineSpec(
    id="demo-v1",
    image="r/i@sha256:abc",
    steps_yaml="steps: []\n",
    steps_sha256=canonical_sha256({"steps": []}),
    legacy_sha256="l" * 64,
)


def _published(sha, digest):
    return {"pipeline_sha256": sha, "image_digest": digest}


def test_fresh_pipeline_ok():
    ok, msg = check_drift(P, None, None)
    assert ok and msg is None


def test_configmap_mismatch_is_error():
    ok, msg = check_drift(P, "steps: [DIFFERENT]\n", None)
    assert not ok and "drift" in msg.lower()


def test_published_sha_mismatch_is_error():
    ok, msg = check_drift(P, P.steps_yaml, _published("x" * 64, P.image))
    assert not ok


def test_published_image_mismatch_is_error():
    ok, msg = check_drift(
        P, P.steps_yaml, _published(P.steps_sha256, "r/i@sha256:OTHER")
    )
    assert not ok


def test_unknown_image_digest_grandfathered_with_warning():
    ok, msg = check_drift(P, P.steps_yaml, _published(P.steps_sha256, "unknown"))
    assert ok and msg is not None and "unknown" in msg


def test_everything_matching_ok():
    ok, msg = check_drift(P, P.steps_yaml, _published(P.steps_sha256, P.image))
    assert ok and msg is None


def test_published_legacy_yaml_sha_still_matches():
    """R10: results published before the canonical hash carry the sha of the
    PyYAML dump; they must not read as drift after the upgrade."""
    ok, msg = check_drift(P, P.steps_yaml, _published(P.legacy_sha256, P.image))
    assert ok and msg is None


def test_published_without_provenance_is_skipped_with_a_warning():
    """R11: a legacy manifest with no pipeline_sha256 must not block the
    pipeline forever; it simply cannot testify."""
    ok, msg = check_drift(P, P.steps_yaml, {"pages": 3})
    assert ok and msg is not None and "provenance" in msg


def test_configmap_compared_by_content_not_serialisation():
    """A ConfigMap written by another PyYAML version (key order, quoting) is
    the same recipe."""
    ok, msg = check_drift(P, "steps:\n  []\n", None)
    assert ok and msg is None
    ok, msg = check_drift(P, "steps: [x]\n", None)
    assert not ok
    ok, msg = check_drift(P, ": not yaml [", None)
    assert not ok
