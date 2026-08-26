import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from htrflow_batch import main as main_mod
from htrflow_batch.main import (
    EXIT_OK,
    EXIT_PERMANENT,
    EXIT_TRANSIENT,
    main,
    publish_failure_metrics,
)
from htrflow_batch.store import ResultStore


@pytest.fixture
def env(tmp_path, cfg, sample_manifest, monkeypatch):
    """Full env + mocked HTTP (manifest + images) + moto S3 via cfg/s3 fixtures."""

    def handler(req):
        if req.url.path.endswith("manifest.json"):
            return httpx.Response(200, json=sample_manifest)
        return httpx.Response(200, content=b"JPEGDATA")

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("steps: []\n")
    return {
        "VOLUME_REF": "SE-RA-1234",
        "IIIF_MANIFEST_URL": "https://iiif.example/mock-vol/manifest.json",
        "PIPELINE_PATH": str(pipeline),
        "PIPELINE_ID": "demo-v1",
        "S3_ENDPOINT": "",
        "S3_BUCKET": "htr-results",
        "PUBLIC_RESULTS_BASE": "http://public/htr-results",
        "WORKDIR_PATH": str(tmp_path / "work"),
        "TERMINATION_LOG_PATH": str(tmp_path / "term.log"),
    }


def fake_factory(cfg):
    """Writes a plausible ALTO per page."""

    def process(path: Path):
        out = Path(cfg.workdir) / "outputs" / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
        )
        return {"alto": out}

    return process


def _keys(s3, cfg):
    resp = s3.list_objects_v2(Bucket=cfg.s3_bucket)
    return sorted(o["Key"] for o in resp.get("Contents", []))


def test_happy_path(env, cfg, s3):
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys
    assert "demo-v1/SE-RA-1234/iiif.json" in keys
    assert "demo-v1/SE-RA-1234/pipeline.yaml" in keys
    assert "demo-v1/SE-RA-1234/manifest.json" in keys
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["pages"] == 3
    assert body["results"]["0001"]["status"] == "ok"
    assert "gpu_stall_seconds" in body and "wall_seconds" in body
    assert body["viewer_url"].endswith("iiif.json")


def test_resume_skips_done(env, cfg, s3):
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/alto/0001.xml",
        Body=b'<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>',
    )
    calls = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            calls.append(path.stem)
            return inner(path)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "0001" not in calls and calls == ["0002", "0003"]
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0001"]["status"] == "skipped"

    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    canvas_names = {c["id"].rsplit("/", 1)[-1] for c in iiif["items"]}
    assert canvas_names == {"0001", "0002", "0003"}  # skipped page not omitted


def test_bad_manifest_is_permanent(env, cfg, s3, monkeypatch):
    def handler(req):
        return httpx.Response(404)

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup"


def test_manifest_5xx_is_transient(env, cfg, s3, monkeypatch):
    """W1: a 503 from the IIIF server is a retry, not needs-attention."""
    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(503))
        ),
    )
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup" and term["permanent"] is False


def test_manifest_over_cap_is_permanent(env, cfg, s3):
    rc = main(dict(env, MANIFEST_MAX_BYTES="10"), process_page_factory=fake_factory)
    assert rc == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert "too large" in term["error"]


def test_page_failure_is_transient_and_blocks_completion(env, cfg, s3):
    def factory(c):
        inner = fake_factory(c)

        def process(path):
            if path.stem == "0002":
                raise RuntimeError("cuda hiccup")
            return inner(path)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_TRANSIENT
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/manifest.json" not in keys  # no false complete
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys  # partials kept
    # the run's timing/stall evidence outlives the pod
    assert "demo-v1/SE-RA-1234/metrics-failed-latest.json" in keys
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify" and "0002" in str(term)


def test_max_pages_caps(env, cfg, s3):
    env = dict(env, MAX_PAGES="2")
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["pages"] == 2


def test_downloader_crash_does_not_hang(env, cfg, s3, monkeypatch):
    """If run_downloader raises before enqueuing its sentinel, consume() must
    still terminate (via the except-path sentinel in main.dl()) instead of
    blocking forever on out_queue.get()."""

    def boom(*a, **k):
        raise OSError("dest_dir mkdir failed")

    monkeypatch.setattr(main_mod, "run_downloader", boom)
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify"


def test_resume_failure_is_attributed_to_resume_stage(env, cfg, s3, monkeypatch):
    def boom(self):
        raise RuntimeError("s3 listing failed")

    monkeypatch.setattr(ResultStore, "done_pages", boom)
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "resume"
    # nothing ran yet, so there is no evidence to publish
    assert "demo-v1/SE-RA-1234/metrics-failed-latest.json" not in _keys(s3, cfg)


def test_terminate_with_long_error_writes_valid_json(tmp_path):
    """A verify-stage failure with a huge missing/failed page list produces
    an `error` string well past the old 4096-byte cutoff. Slicing the
    *serialized* JSON (`json.dumps(reason)[:4096]`) can cut mid-string and
    write invalid JSON to the termination log; the fix truncates the field
    before serializing instead."""
    log_path = tmp_path / "term.log"
    huge_missing = [f"{i:04d}" for i in range(1000)]
    long_error = f"verify failed: missing={huge_missing} failed=[]"
    assert len(json.dumps({"error": long_error})) > 4096  # actually exercises the bug

    main_mod._terminate(
        {"TERMINATION_LOG_PATH": str(log_path)},
        {"stage": "verify", "permanent": False, "error": long_error},
    )

    term = json.loads(log_path.read_text())  # must not raise
    assert term["stage"] == "verify"
    assert term["error"].startswith("verify failed: missing=")


def test_publish_warns_when_viewer_manifest_incomplete(env, cfg, s3, caplog):
    """When some pages' ALTO dims can't be parsed, iiif.json still publishes
    for the pages it can, but the wrapper must log that the viewer manifest
    is incomplete rather than silently dropping canvases."""

    def factory(c):
        def process(path: Path):
            out = Path(c.workdir) / "outputs" / "alto" / f"{path.stem}.xml"
            out.parent.mkdir(parents=True, exist_ok=True)
            if path.stem == "0002":
                out.write_text(
                    "<alto><Layout><Page/></Layout></alto>"
                )  # no WIDTH/HEIGHT
            else:
                out.write_text(
                    '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
                )
            return {"alto": out}

        return process

    with caplog.at_level("WARNING"):
        rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "viewer manifest covers 2/3 pages" in caplog.text
    keys = _keys(s3, cfg)
    assert (
        "demo-v1/SE-RA-1234/iiif.json" in keys
    )  # still published for the 2 good pages


def test_publish_warns_when_no_dims_resolved(env, cfg, s3, caplog):
    """When no page's ALTO dims can be parsed, iiif.json is skipped entirely
    while manifest.json still advertises a viewer_url; the wrapper must warn
    that the viewer URL will 404 instead of failing silently."""

    def factory(c):
        def process(path: Path):
            out = Path(c.workdir) / "outputs" / "alto" / f"{path.stem}.xml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("<alto><Layout><Page/></Layout></alto>")  # no WIDTH/HEIGHT
            return {"alto": out}

        return process

    with caplog.at_level("WARNING"):
        rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "viewer_url will 404" in caplog.text
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/iiif.json" not in keys
    assert "demo-v1/SE-RA-1234/manifest.json" in keys


def test_publish_failure_metrics_records_run_evidence():
    """A failed run must leave its timing/stall evidence in the bucket
    (spec §4.8) — today it dies with the pod."""
    calls = []
    store = SimpleNamespace(put_json=lambda key, obj: calls.append((key, obj)))
    cfg = SimpleNamespace(volume_ref="vol-x", pipeline_id="demo-v1")
    stats = SimpleNamespace(
        stall_seconds=12.34,
        results={
            "0001": SimpleNamespace(status="ok", seconds=3.2, error=None),
            "0002": SimpleNamespace(status="failed", seconds=9.9, error="HTTP 400"),
        },
    )
    publish_failure_metrics(store, cfg, stats, 100.0, "verify", "verify failed: x")
    assert len(calls) == 1
    (key, obj) = calls[0]
    assert key == "metrics-failed-latest.json"
    assert obj["stage"] == "verify"
    assert obj["gpu_stall_seconds"] == 12.3
    assert obj["results"]["0002"]["error"] == "HTTP 400"


def test_publish_failure_metrics_never_raises(caplog):
    def boom(key, obj):
        raise OSError("bucket gone")

    store = SimpleNamespace(put_json=boom)
    cfg = SimpleNamespace(volume_ref="v", pipeline_id="p")
    stats = SimpleNamespace(stall_seconds=0.0, results={})
    with caplog.at_level("WARNING"):
        publish_failure_metrics(store, cfg, stats, 1.0, "stream", "x")  # must not raise
    assert "could not publish failure metrics" in caplog.text


def test_setup_creates_the_writable_dirs_before_model_load(env, cfg, s3, tmp_path):
    """Under readOnlyRootFilesystem HOME/TMPDIR/YOLO_CONFIG_DIR point into the
    tmpfs workdir; they must exist before anything in htrflow's stack tries
    to write a settings file or JIT cache there."""
    work = tmp_path / "work"
    env = {
        **env,
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "YOLO_CONFIG_DIR": str(work / "ultralytics"),
    }
    seen = {}

    def factory(cfg):
        seen.update(
            {k: Path(env[k]).is_dir() for k in ("HOME", "TMPDIR", "YOLO_CONFIG_DIR")}
        )
        return fake_factory(cfg)

    assert main(env, process_page_factory=factory) == EXIT_OK
    assert seen == {"HOME": True, "TMPDIR": True, "YOLO_CONFIG_DIR": True}


def test_run_log_is_shipped_to_the_status_tree(env, cfg, s3):
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    body = (
        s3.get_object(Bucket=cfg.s3_bucket, Key="status/logs/demo-v1/SE-RA-1234.txt")[
            "Body"
        ]
        .read()
        .decode()
    )
    assert "3 pages in manifest" in body  # wrapper logging
    assert "COMPLETE 3 pages" in body  # the final upload includes the last line
    assert "status/logs/demo-v1/SE-RA-1234.txt" not in [
        k for k in _keys(s3, cfg) if k.startswith("demo-v1/")
    ]


def test_run_log_shipping_can_be_disabled(env, cfg, s3):
    rc = main(dict(env, LOG_SHIP_SECONDS="0"), process_page_factory=fake_factory)
    assert rc == EXIT_OK
    # interval 0 = no periodic thread, but the final upload still happens
    assert "status/logs/demo-v1/SE-RA-1234.txt" in _keys(s3, cfg)


def test_streams_are_restored_after_main(env, cfg, s3):
    import sys

    before = (sys.stdout, sys.stderr)
    main(env, process_page_factory=fake_factory)
    assert (sys.stdout, sys.stderr) == before
