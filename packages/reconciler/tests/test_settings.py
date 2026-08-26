"""Env contract of the CronJob entrypoint: the chart sets these names, so a
rename here silently breaks the deployment unless a test pins them."""

import pytest
from pydantic import ValidationError

from htrflow_reconciler.__main__ import (
    Settings,
    _git_timeout,
    _internal_results_base,
    build_config,
)
from htrflow_reconciler.jobspec import ReconcilerConfig

REQUIRED = {
    "CAMPAIGNS_REPO_URL": "https://example.invalid/campaigns.git",
    "PUBLIC_RESULTS_BASE": "http://pub/htr-results",
}


@pytest.fixture
def env(monkeypatch):
    def _set(**extra):
        for k, v in {**REQUIRED, **extra}.items():
            monkeypatch.setenv(k, v)
        return Settings()

    return _set


def test_defaults_reproduce_the_poc(env):
    s = env()
    assert s.campaigns_repo_url == REQUIRED["CAMPAIGNS_REPO_URL"]
    assert s.public_results_base == REQUIRED["PUBLIC_RESULTS_BASE"]
    assert (s.reconciler_namespace, s.reconciler_queue) == ("htr-batch", "htr-batch")
    assert s.reconciler_s3_secret == "htr-batch-s3"
    assert s.reconciler_data_pvc == "htr-test-data"
    assert (s.reconciler_window, s.reconciler_attempt_cap) == (20, 3)


def test_cluster_coupling_is_env_overridable(env):
    s = env(
        RECONCILER_NAMESPACE="htr-prod",
        RECONCILER_QUEUE="htr-prod-q",
        RECONCILER_S3_SECRET="prod-s3",
        RECONCILER_DATA_PVC="prod-data",
        RECONCILER_WINDOW="5",
        RECONCILER_ATTEMPT_CAP="1",
    )
    cfg = ReconcilerConfig(
        public_results_base=s.public_results_base,
        namespace=s.reconciler_namespace,
        queue=s.reconciler_queue,
        s3_secret=s.reconciler_s3_secret,
        data_pvc=s.reconciler_data_pvc,
        window=s.reconciler_window,
        attempt_cap=s.reconciler_attempt_cap,
    )
    assert cfg.namespace == "htr-prod"
    assert cfg.queue == "htr-prod-q"
    assert cfg.s3_secret == "prod-s3"
    assert cfg.data_pvc == "prod-data"
    assert (cfg.window, cfg.attempt_cap) == (5, 1)


def test_missing_required_env_fails_fast(monkeypatch):
    monkeypatch.delenv("CAMPAIGNS_REPO_URL", raising=False)
    monkeypatch.delenv("PUBLIC_RESULTS_BASE", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_repo_web_url_defaults_empty(env):
    s = env()
    assert s.campaigns_repo_web_url == ""


def test_repo_web_url_env_populates_setting(env):
    s = env(CAMPAIGNS_REPO_WEB_URL="https://github.com/example/campaigns")
    assert s.campaigns_repo_web_url == "https://github.com/example/campaigns"


def test_internal_results_base_from_s3_endpoint(env):
    s = env(S3_ENDPOINT="http://rustfs.ns.svc:9000/", S3_BUCKET="htr-results")
    assert _internal_results_base(s) == "http://rustfs.ns.svc:9000/htr-results"


def test_internal_results_base_empty_without_endpoint(env):
    s = env()
    assert _internal_results_base(s) == ""


def test_git_timeout_never_exceeds_the_tick_deadline(env):
    """O7: a clone that outlives the CronJob's activeDeadlineSeconds is killed
    without a log line; clamping keeps the failure reportable."""
    assert _git_timeout(env()) == 300
    assert _git_timeout(env(RECONCILER_TICK_DEADLINE_SECONDS="120")) == 120
    assert _git_timeout(env(RECONCILER_GIT_TIMEOUT="60")) == 60
    s = env(RECONCILER_GIT_TIMEOUT="900", RECONCILER_TICK_DEADLINE_SECONDS="600")
    assert _git_timeout(s) == 600


def test_new_env_defaults_match_the_remediation_plan(env):
    """The chart renders these names; the defaults are the contract."""
    cfg = build_config(env())
    assert cfg.tick_seconds == 300
    assert cfg.tick_deadline_seconds == 600
    assert cfg.lease_name == "htr-reconciler"
    assert cfg.allowed_image_repos == ()
    assert cfg.require_model_revision is False
    assert cfg.job_min_deadline_seconds == 21600
    assert cfg.job_seconds_per_page == 30
    assert cfg.job_runtime_class == "nvidia"
    assert cfg.job_node_selector == {}
    assert cfg.job_tolerations == []
    assert cfg.max_validations_per_tick == 50
    assert env().reconciler_fetch_max_bytes == 16777216


def test_new_env_is_read(env):
    s = env(
        RECONCILER_TICK_SECONDS="120",
        RECONCILER_TICK_DEADLINE_SECONDS="900",
        RECONCILER_LEASE_NAME="htr-rec-prod",
        RECONCILER_ALLOWED_IMAGE_REPOS=(
            "ghcr.io/riksarkivet/, docker.io/riksarkivet/htrflow-batch"
        ),
        RECONCILER_REQUIRE_MODEL_REVISION="true",
        RECONCILER_JOB_MIN_DEADLINE_SECONDS="3600",
        RECONCILER_JOB_SECONDS_PER_PAGE="45",
        RECONCILER_JOB_RUNTIME_CLASS="",
        RECONCILER_JOB_NODE_SELECTOR='{"gpu": "gb10"}',
        RECONCILER_JOB_TOLERATIONS='[{"key": "gpu", "operator": "Exists"}]',
        RECONCILER_MAX_VALIDATIONS_PER_TICK="5",
        RECONCILER_FETCH_MAX_BYTES="1024",
    )
    cfg = build_config(s)
    assert cfg.tick_seconds == 120
    assert cfg.tick_deadline_seconds == 900
    assert cfg.lease_name == "htr-rec-prod"
    assert cfg.allowed_image_repos == (
        "ghcr.io/riksarkivet/",
        "docker.io/riksarkivet/htrflow-batch",
    )
    assert cfg.require_model_revision is True
    assert cfg.job_min_deadline_seconds == 3600
    assert cfg.job_seconds_per_page == 45
    assert cfg.job_runtime_class == ""
    assert cfg.job_node_selector == {"gpu": "gb10"}
    assert cfg.job_tolerations == [{"key": "gpu", "operator": "Exists"}]
    assert cfg.max_validations_per_tick == 5
    assert s.reconciler_fetch_max_bytes == 1024


def test_build_config_carries_the_existing_settings(env):
    s = env(
        S3_ENDPOINT="http://rustfs.ns.svc:9000",
        RECONCILER_NAMESPACE="ns1",
        RECONCILER_WINDOW="7",
    )
    cfg = build_config(s)
    assert cfg.internal_results_base == "http://rustfs.ns.svc:9000/htr-results"
    assert cfg.namespace == "ns1" and cfg.window == 7
    assert cfg.campaigns_repo_url == REQUIRED["CAMPAIGNS_REPO_URL"]


def test_wrapper_byte_cap_env_is_wired(env):
    cfg = build_config(env())
    assert cfg.job_manifest_max_bytes == 16777216
    assert cfg.job_fetch_max_bytes == 67108864
    cfg = build_config(
        env(RECONCILER_JOB_MANIFEST_MAX_BYTES="1", RECONCILER_JOB_FETCH_MAX_BYTES="2")
    )
    assert (cfg.job_manifest_max_bytes, cfg.job_fetch_max_bytes) == (1, 2)
