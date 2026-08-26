from htrflow_reconciler import s3


def test_key_layout_matches_wrapper_contract():
    assert s3.manifest_key("demo-v1", "R1") == "demo-v1/R1/manifest.json"
    # R9: the key carries a hash of the image list, so an images: edit in
    # git produces a new manifest instead of being ignored forever.
    key = s3.synthetic_manifest_key("demo-v1", "loose", ["http://x/1.jpg"])
    assert key.startswith("sources/demo-v1/loose/") and key.endswith("/manifest.json")
    assert key == s3.synthetic_manifest_key("demo-v1", "loose", ["http://x/1.jpg"])
    assert key != s3.synthetic_manifest_key("demo-v1", "loose", ["http://x/2.jpg"])
    assert s3.failure_log_key("demo-v1", "R1") == "status/failures/demo-v1/R1.txt"
    assert s3.status_key() == "status/status.json"
    assert s3.attempts_key() == "status/attempts.json"
    assert s3.validation_key() == "status/validation.json"
