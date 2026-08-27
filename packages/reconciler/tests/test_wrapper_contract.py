"""Reconciler <-> wrapper contract (audit T5): the literals the two packages
share — the Job env, the run-log key, exit codes, the image-digest sentinel,
the drift hash — are asserted against the wrapper package itself (a
workspace import), not re-typed here. Run from the workspace root
(``uv run --all-packages pytest``): the shared venv carries both members.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import boto3
import httpx
import pytest
from htrflow_batch import main as wrapper_main
from htrflow_batch.config import _REQUIRED, Config
from htrflow_batch.main import EXIT_PERMANENT, EXIT_SIGTERM
from htrflow_batch.main import main as wrapper_run
from htrflow_batch.store import ResultStore
from moto import mock_aws

from htrflow_reconciler import s3 as keys
from htrflow_reconciler.guards import check_drift
from htrflow_reconciler.jobspec import ReconcilerConfig, build_job
from htrflow_reconciler.models import Volume
from htrflow_reconciler.parse import parse_pipeline
from htrflow_reconciler.status import JobState, is_permanent

PID = "demo-v1"
VID = "R0000001"
IMAGE = "r/htrflow-batch@sha256:0123456789abcdef"
CFG = ReconcilerConfig(
    public_results_base="http://public/htr-results", allowed_image_repos=("r/",)
)
SPEC = parse_pipeline(
    PID,
    f"image: {IMAGE}\nsteps:\n  - step: Segmentation\n",
    allowed_repos=("r/",),
)
VOLUME = Volume(id=VID, manifest_url="https://iiif.example/vol/manifest.json")

#: The Job's S3 Secret keys, as the chart renders them (secretKeyRef).
SECRET = {"S3_ENDPOINT": "", "S3_BUCKET": "htr-results"}


def _job_env(job: dict) -> dict[str, str]:
    """The wrapper container's env as the kubelet would resolve it."""
    env: dict[str, str] = {}
    for item in job["spec"]["template"]["spec"]["containers"][0]["env"]:
        if "value" in item:
            env[item["name"]] = item["value"]
        else:
            ref = item["valueFrom"]["secretKeyRef"]
            assert ref["name"] == CFG.s3_secret
            env[item["name"]] = SECRET[ref["key"]]
    return env


# -- Job env ------------------------------------------------------------------


def test_job_env_satisfies_the_wrapper_required_set():
    env = _job_env(build_job(SPEC, VOLUME, VOLUME.manifest_url, CFG))
    missing = [name for _, name in _REQUIRED if not env.get(name)]
    assert missing == []
    cfg = Config.from_env(env)
    assert (cfg.volume_ref, cfg.pipeline_id) == (VID, PID)
    assert cfg.manifest_url == VOLUME.manifest_url
    assert cfg.public_results_base == CFG.public_results_base
    assert cfg.pipeline_path == "/config/pipeline.yaml"
    assert cfg.manifest_max_bytes == CFG.job_manifest_max_bytes
    assert cfg.fetch_max_bytes == CFG.job_fetch_max_bytes
    assert cfg.workdir == "/work"


def test_wrapper_result_prefix_is_where_done_detection_looks():
    """Done = ``<pipeline>/<volume>/manifest.json`` exists. The wrapper's
    prefix comes from S3_PREFIX + PIPELINE_ID + VOLUME_REF, so the Job must
    pin S3_PREFIX empty whatever the Secret carries."""
    env = _job_env(build_job(SPEC, VOLUME, VOLUME.manifest_url, CFG))
    cfg = Config.from_env(env)
    assert f"{cfg.volume_prefix}/manifest.json" == keys.manifest_key(PID, VID)
    assert cfg.s3_prefix == ""


# -- run-log key ---------------------------------------------------------------


def test_run_log_key_agrees(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    env = _job_env(build_job(SPEC, VOLUME, VOLUME.manifest_url, CFG))
    store = ResultStore(Config.from_env(env))
    assert store.run_log_key() == keys.run_log_key(PID, VID)


# -- exit codes ----------------------------------------------------------------


def test_exit_permanent_is_the_pod_failure_policy_and_is_permanent_code():
    job = build_job(SPEC, VOLUME, VOLUME.manifest_url, CFG)
    rules = job["spec"]["podFailurePolicy"]["rules"]
    fail = next(r for r in rules if r["action"] == "FailJob")
    assert fail["onExitCodes"]["values"] == [EXIT_PERMANENT]
    assert fail["onExitCodes"]["containerName"] == "wrapper"
    assert is_permanent(JobState(active=False, failed=True, exit_code=EXIT_PERMANENT))
    assert not is_permanent(JobState(active=False, failed=True, exit_code=EXIT_SIGTERM))
    assert not is_permanent(JobState(active=False, failed=True, exit_code=1))


def test_sigterm_exit_matches_the_reconcilers_progress_rule():
    """main.py's ``_made_progress`` compares against the literal 143 (the
    wrapper's EXIT_SIGTERM); a change on either side must fail here."""
    assert EXIT_SIGTERM == 143


# -- image digest sentinel and drift hash: the real wrapper run ----------------


ALTO = '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
PAGE = '<PcGts><Page imageWidth="2500" imageHeight="3538"/></PcGts>'


def _manifest() -> dict:
    service = "https://iiif.example/vol/page-1"
    return {
        "id": VOLUME.manifest_url,
        "type": "Manifest",
        "items": [
            {
                "id": f"{service}/canvas",
                "type": "Canvas",
                "width": 2500,
                "height": 3538,
                "items": [
                    {
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "type": "Annotation",
                                "motivation": "painting",
                                "body": {
                                    "id": f"{service}/full/max/0/default.jpg",
                                    "type": "Image",
                                    "service": [
                                        {"id": service, "type": "ImageService3"}
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _factory(cfg: Config):
    def process(image: Path) -> dict[str, Path]:
        files = {}
        for fmt, text in (("alto", ALTO), ("page", PAGE)):
            out = Path(cfg.workdir) / "outputs" / fmt / f"{image.stem}.xml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            files[fmt] = out
        return files

    return process


@pytest.fixture
def wrapper_env(tmp_path, monkeypatch):
    """The Job env with the reconciler-rendered ConfigMap on disk, a mocked
    IIIF host and a moto bucket: enough to run the real wrapper end to end."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    def handler(req):
        if req.url.path.endswith("manifest.json"):
            return httpx.Response(200, json=_manifest())
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

    monkeypatch.setattr(
        wrapper_main,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(SPEC.steps_yaml)  # exactly the ConfigMap text
    env = _job_env(build_job(SPEC, VOLUME, VOLUME.manifest_url, CFG))
    env.update(
        PIPELINE_PATH=str(pipeline),
        WORKDIR_PATH=str(tmp_path / "work"),
        HOME=str(tmp_path / "home"),
        TMPDIR=str(tmp_path / "tmp"),
        YOLO_CONFIG_DIR=str(tmp_path / "yolo"),
        TERMINATION_LOG_PATH=str(tmp_path / "term.log"),
        LOG_SHIP_SECONDS="0",
    )
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=SECRET["S3_BUCKET"])
        yield env, client


def _published(client) -> dict:
    body = client.get_object(Bucket="htr-results", Key=keys.manifest_key(PID, VID))
    return json.loads(body["Body"].read())


def test_published_manifest_passes_the_drift_guard(wrapper_env):
    """The wrapper hashes the ConfigMap text; the guard accepts that
    (legacy) sha and the pinned image the Job passed as IMAGE_DIGEST."""
    env, client = wrapper_env
    assert wrapper_run(env, process_page_factory=_factory) == 0
    published = _published(client)
    assert published["pipeline_sha256"] == SPEC.legacy_sha256
    assert published["image_digest"] == SPEC.image
    assert check_drift(SPEC, SPEC.steps_yaml, published) == (True, None)


def test_unknown_image_digest_sentinel_is_grandfathered(wrapper_env):
    """A wrapper started without IMAGE_DIGEST publishes ``"unknown"``; the
    guard must read that exact string as pre-pinning, not as drift."""
    env, client = wrapper_env
    del env["IMAGE_DIGEST"]
    assert wrapper_run(env, process_page_factory=_factory) == 0
    published = _published(client)
    assert published["image_digest"] == "unknown"
    ok, msg = check_drift(SPEC, SPEC.steps_yaml, published)
    assert ok and msg is not None and "grandfathered" in msg


# -- terminal log lines vs the frontend ----------------------------------------

#: Copied from frontend/src/lib/runlog.ts (TERMINAL_RE). The campaign browser
#: stops polling a run log once its tail matches this; the wrapper's last
#: line on each exit path must keep matching. Keep the two literals in step.
FRONTEND_TERMINAL_RE = re.compile(
    r"\] COMPLETE \d+ pages|(permanent|transient) failure in \w+:"
)


def test_success_terminal_line_matches_the_frontend_regex(wrapper_env, caplog):
    env, _ = wrapper_env
    with caplog.at_level(logging.INFO, logger="htrflow_batch"):
        assert wrapper_run(env, process_page_factory=_factory) == 0
    last = [r.getMessage() for r in caplog.records if "COMPLETE" in r.getMessage()]
    assert last and FRONTEND_TERMINAL_RE.search(last[-1])


def test_permanent_terminal_line_matches_the_frontend_regex(wrapper_env, caplog):
    env, _ = wrapper_env
    with caplog.at_level(logging.ERROR, logger="htrflow_batch"):
        assert wrapper_run({}, process_page_factory=_factory) == EXIT_PERMANENT
    lines = [r.getMessage() for r in caplog.records if "failure in" in r.getMessage()]
    assert lines and FRONTEND_TERMINAL_RE.search(lines[-1])
    assert lines[-1].startswith("permanent failure in setup:")


def test_transient_terminal_line_matches_the_frontend_regex(
    wrapper_env, caplog, monkeypatch
):
    env, _ = wrapper_env

    def down(*a, **kw):
        raise httpx.ConnectError("iiif host down")

    monkeypatch.setattr(wrapper_main, "fetch_manifest", down)
    with caplog.at_level(logging.ERROR, logger="htrflow_batch"):
        assert wrapper_run(env, process_page_factory=_factory) == 1
    lines = [r.getMessage() for r in caplog.records if "failure in" in r.getMessage()]
    assert lines and FRONTEND_TERMINAL_RE.search(lines[-1])
    assert lines[-1].startswith("transient failure in setup:")
