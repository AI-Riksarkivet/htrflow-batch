from htrflow_reconciler.guards import check_drift
from htrflow_reconciler.models import PipelineSpec

P = PipelineSpec(
    id="demo-v1",
    image="r/i@sha256:abc",
    steps_yaml="steps: []\n",
    steps_sha256="s" * 64,
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
