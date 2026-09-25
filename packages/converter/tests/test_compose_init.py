"""scripts/compose_init.py: the compose smoke stack's bucket setup.

`main()` reaches network and a subprocess, so every collaborator is faked
here (boto3's S3 client, httpx, subprocess.run) and only the calls onto them
are asserted -- the same shape `test_chart_render.py` checks against the
devstack chart's init hook, which this script mirrors (module docstring).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import compose_init  # noqa: E402


class FakeS3:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.policies: dict[str, str] = {}
        self.cors: dict[str, dict] = {}
        self.exceptions = SimpleNamespace(BucketAlreadyOwnedByYou=RuntimeError)

    def create_bucket(self, Bucket: str) -> None:
        self.created.append(Bucket)

    def put_object(self, **kwargs) -> None:
        pass

    def put_bucket_policy(self, Bucket: str, Policy: str) -> None:
        self.policies[Bucket] = Policy

    def put_bucket_cors(self, Bucket: str, CORSConfiguration: dict) -> None:
        self.cors[Bucket] = CORSConfiguration


class FakeResponse:
    content = b"fake-jpeg-bytes"

    def raise_for_status(self) -> None:
        pass


def _run_main(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    """Reload the module (so its env-derived constants, set by the test's own
    `monkeypatch.setenv` calls, are recomputed), stub every collaborator
    `main()` reaches, and run it."""
    importlib.reload(compose_init)
    s3 = FakeS3()
    monkeypatch.setattr(compose_init.boto3, "client", lambda *a, **k: s3)
    monkeypatch.setattr(compose_init.httpx, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(
        compose_init.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout='{"mock": "manifest"}'),
    )
    compose_init.main()
    return s3


def test_the_image_cache_bucket_is_created_with_no_policy_or_cors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("IMAGE_CACHE_BUCKET", "images-batch")
    s3 = _run_main(monkeypatch)

    assert "images-batch" in s3.created
    assert "images-batch" not in s3.policies
    assert "images-batch" not in s3.cors
    # the other two buckets keep their own policy and CORS, unaffected
    assert set(s3.policies) == {
        compose_init.FIXTURES_BUCKET,
        compose_init.RESULTS_BUCKET,
    }
    assert set(s3.cors) == {compose_init.FIXTURES_BUCKET, compose_init.RESULTS_BUCKET}


def test_without_the_env_var_no_third_bucket_is_created(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("IMAGE_CACHE_BUCKET", raising=False)
    s3 = _run_main(monkeypatch)

    assert compose_init.IMAGE_CACHE_BUCKET == ""
    assert set(s3.created) == {
        compose_init.FIXTURES_BUCKET,
        compose_init.RESULTS_BUCKET,
    }


def test_the_compose_stack_passes_one_image_cache_knob_to_init_and_wrapper():
    """The compose stack has no converter, so HTR_IMAGE_CACHE_BUCKET is the
    one switch: the init creates the bucket, the wrapper uses it. Empty (the
    default) is off, so the stack behaves as it did without the cache."""
    import yaml

    compose = yaml.safe_load((ROOT / ".docker" / "docker-compose.yml").read_text())
    for name in ("fixtures-init", "wrapper"):
        env = compose["services"][name]["environment"]
        assert env["IMAGE_CACHE_BUCKET"] == "${HTR_IMAGE_CACHE_BUCKET:-}", name
