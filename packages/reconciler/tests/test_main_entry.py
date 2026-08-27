"""The CronJob entrypoint's wiring (audit T7): ``run()`` is the only place
Settings meet the real adapters, and the only untested 30 lines that could
send a production tick to the wrong bucket. Adapters are replaced at the
module seam; every env in the plan's contract table is driven through
``Settings`` -> ``build_config`` -> ``tick`` as a table."""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path

import pytest

from htrflow_reconciler import __main__ as entry
from htrflow_reconciler.jobspec import ReconcilerConfig
from htrflow_reconciler.s3 import Bucket

REQUIRED = {
    "CAMPAIGNS_REPO_URL": "git://git-daemon.htr-batch.svc/campaigns-local.git",
    "PUBLIC_RESULTS_BASE": "http://localhost:30900/htr-results",
}


class _Seams:
    """Records what run() hands to each adapter."""

    def __init__(self):
        self.checkout = None
        self.boto = None
        self.cluster_ns = None
        self.tick = None


@pytest.fixture
def seams(monkeypatch, tmp_path):
    rec = _Seams()

    def checkout(url, dest, *, timeout):
        rec.checkout = (url, Path(dest), timeout)
        return Path(dest)

    def boto_client(service, *, endpoint_url):
        rec.boto = (service, endpoint_url)
        return object()

    class Cluster:
        def __init__(self, namespace):
            rec.cluster_ns = namespace

    def tick(repo, bucket, cluster, cfg, now, fetch_json=None):
        rec.tick = (repo, bucket, cluster, cfg, now, fetch_json)
        return {}

    monkeypatch.setattr(entry, "checkout", checkout)
    monkeypatch.setattr(entry.boto3, "client", boto_client)
    monkeypatch.setattr(entry, "Cluster", Cluster)
    monkeypatch.setattr(entry, "tick", tick)
    monkeypatch.setenv("CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    return rec


def test_run_wires_settings_into_every_adapter(seams, monkeypatch, tmp_path):
    monkeypatch.setenv("S3_ENDPOINT", "http://rustfs.htr-batch.svc:9000/")
    monkeypatch.setenv("S3_BUCKET", "results-prod")
    monkeypatch.setenv("RECONCILER_NAMESPACE", "htr-prod")
    monkeypatch.setenv("RECONCILER_FETCH_MAX_BYTES", "4096")
    entry.run()

    url, dest, timeout = seams.checkout
    assert url == REQUIRED["CAMPAIGNS_REPO_URL"]
    assert dest == tmp_path / "campaigns"
    assert timeout == 300  # default git timeout, under the 600 s deadline

    assert seams.boto == ("s3", "http://rustfs.htr-batch.svc:9000/")
    assert seams.cluster_ns == "htr-prod"

    repo, bucket, cluster, cfg, now, fetch_json = seams.tick
    assert repo == dest
    assert isinstance(bucket, Bucket) and bucket.bucket == "results-prod"
    assert isinstance(cfg, ReconcilerConfig)
    assert cfg.namespace == "htr-prod"
    assert cfg.internal_results_base == "http://rustfs.htr-batch.svc:9000/results-prod"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now)
    assert isinstance(fetch_json, partial) and fetch_json.func is entry.fetch_json
    assert fetch_json.keywords == {"max_bytes": 4096}


def test_run_without_an_endpoint_uses_the_provider_chain(seams, monkeypatch):
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    entry.run()
    assert seams.boto == ("s3", None)
    cfg = seams.tick[3]
    assert cfg.internal_results_base == ""


def test_run_clamps_the_git_timeout_to_the_tick_deadline(seams, monkeypatch):
    """O7: a clone must not outlive the CronJob's activeDeadlineSeconds."""
    monkeypatch.setenv("RECONCILER_GIT_TIMEOUT", "900")
    monkeypatch.setenv("RECONCILER_TICK_DEADLINE_SECONDS", "120")
    entry.run()
    assert seams.checkout[2] == 120
    assert seams.tick[3].tick_deadline_seconds == 120


def test_run_fails_fast_without_required_env(seams, monkeypatch):
    monkeypatch.delenv("CAMPAIGNS_REPO_URL")
    with pytest.raises(Exception):
        entry.run()
    assert seams.tick is None and seams.checkout is None


#: The plan's "Reconciler env" contract table (plus A1's additions), as
#: (env, value, ReconcilerConfig attribute, expected). The chart renders the
#: names on the left; a rename on either side fails here.
PLAN_TABLE = [
    ("RECONCILER_TICK_SECONDS", "120", "tick_seconds", 120),
    ("RECONCILER_TICK_DEADLINE_SECONDS", "900", "tick_deadline_seconds", 900),
    ("RECONCILER_DATA_PVC", "models-pvc", "data_pvc", "models-pvc"),
    (
        "RECONCILER_ALLOWED_IMAGE_REPOS",
        "ghcr.io/riksarkivet/, 127.0.0.1:30500/",
        "allowed_image_repos",
        ("ghcr.io/riksarkivet/", "127.0.0.1:30500/"),
    ),
    ("RECONCILER_REQUIRE_MODEL_REVISION", "true", "require_model_revision", True),
    ("RECONCILER_JOB_MIN_DEADLINE_SECONDS", "3600", "job_min_deadline_seconds", 3600),
    ("RECONCILER_JOB_SECONDS_PER_PAGE", "45", "job_seconds_per_page", 45),
    ("RECONCILER_JOB_RUNTIME_CLASS", "", "job_runtime_class", ""),
    ("RECONCILER_JOB_RUNTIME_CLASS", "nvidia", "job_runtime_class", "nvidia"),
    (
        "RECONCILER_JOB_NODE_SELECTOR",
        '{"kubernetes.io/arch": "arm64"}',
        "job_node_selector",
        {"kubernetes.io/arch": "arm64"},
    ),
    (
        "RECONCILER_JOB_TOLERATIONS",
        '[{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]',
        "job_tolerations",
        [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
    ),
    ("RECONCILER_MAX_VALIDATIONS_PER_TICK", "5", "max_validations_per_tick", 5),
    ("RECONCILER_LEASE_NAME", "htr-rec-prod", "lease_name", "htr-rec-prod"),
    ("RECONCILER_JOB_MANIFEST_MAX_BYTES", "8388608", "job_manifest_max_bytes", 8388608),
    ("RECONCILER_JOB_FETCH_MAX_BYTES", "33554432", "job_fetch_max_bytes", 33554432),
    ("RECONCILER_NAMESPACE", "ns1", "namespace", "ns1"),
    ("RECONCILER_QUEUE", "q1", "queue", "q1"),
    ("RECONCILER_S3_SECRET", "s3-prod", "s3_secret", "s3-prod"),
    ("RECONCILER_WINDOW", "7", "window", 7),
    ("RECONCILER_ATTEMPT_CAP", "1", "attempt_cap", 1),
    (
        "CAMPAIGNS_REPO_WEB_URL",
        "https://gh/o/r",
        "campaigns_repo_web_url",
        "https://gh/o/r",
    ),
]


@pytest.mark.parametrize("env,value,attr,expected", PLAN_TABLE)
def test_every_plan_env_reaches_the_tick_config(
    seams, monkeypatch, env, value, attr, expected
):
    monkeypatch.setenv(env, value)
    entry.run()
    assert getattr(seams.tick[3], attr) == expected


def test_reconciler_fetch_max_bytes_bounds_the_reconcilers_own_fetch(
    seams, monkeypatch
):
    """Not a ReconcilerConfig field: it parameterises fetch_json directly."""
    monkeypatch.setenv("RECONCILER_FETCH_MAX_BYTES", "1")
    entry.run()
    assert seams.tick[5].keywords == {"max_bytes": 1}


def test_plan_table_covers_every_reconciler_setting():
    """A new RECONCILER_* setting must be added to the table above (or be
    consumed by run() directly, like RECONCILER_FETCH_MAX_BYTES)."""
    covered = {env for env, *_ in PLAN_TABLE} | {"RECONCILER_FETCH_MAX_BYTES"}
    covered |= {"RECONCILER_GIT_TIMEOUT"}  # clamped into checkout(), tested above
    settings = {name.upper() for name in entry.Settings.model_fields}
    assert {s for s in settings if s.startswith("RECONCILER_")} <= covered
