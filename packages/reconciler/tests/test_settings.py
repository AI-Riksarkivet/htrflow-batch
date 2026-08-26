"""Env contract of the CronJob entrypoint: the chart sets these names, so a
rename here silently breaks the deployment unless a test pins them."""

import pytest
from pydantic import ValidationError

from htrflow_reconciler.__main__ import (
    Settings,
    _git_timeout,
    _internal_results_base,
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
