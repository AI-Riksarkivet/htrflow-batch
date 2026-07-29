from htrflow_reconciler import s3


def test_key_layout_matches_wrapper_contract():
    assert s3.manifest_key("demo-v1", "R1") == "demo-v1/R1/manifest.json"
    assert s3.synthetic_manifest_key("demo-v1", "loose") == (
        "sources/demo-v1/loose/manifest.json"
    )
    assert s3.failure_log_key("demo-v1", "R1") == "status/failures/demo-v1/R1.txt"
    assert s3.status_key() == "status/status.json"
    assert s3.attempts_key() == "status/attempts.json"
    assert s3.validation_key() == "status/validation.json"
