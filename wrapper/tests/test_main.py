import json
from pathlib import Path

import httpx
import pytest
from htrflow_batch import main as main_mod
from htrflow_batch.main import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, main
from htrflow_batch.store import ResultStore


@pytest.fixture
def env(tmp_path, cfg, sample_manifest, monkeypatch):
    """Full env + mocked HTTP (manifest + images) + moto S3 via cfg/s3 fixtures."""
    def handler(req):
        if req.url.path.endswith("manifest.json"):
            return httpx.Response(200, json=sample_manifest)
        return httpx.Response(200, content=b"JPEGDATA")
    monkeypatch.setattr(main_mod, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
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
        out.write_text('<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/>'
                       "</Layout></alto>")
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
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
    assert body["pages"] == 3
    assert body["results"]["0001"]["status"] == "ok"
    assert "gpu_stall_seconds" in body and "wall_seconds" in body
    assert body["viewer_url"].endswith("iiif.json")


def test_resume_skips_done(env, cfg, s3):
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/alto/0001.xml",
        Body=b'<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>')
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
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
    assert body["results"]["0001"]["status"] == "skipped"

    iiif = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/iiif.json")["Body"].read())
    canvas_names = {c["id"].rsplit("/", 1)[-1] for c in iiif["items"]}
    assert canvas_names == {"0001", "0002", "0003"}   # skipped page not omitted


def test_bad_manifest_is_permanent(env, cfg, s3, monkeypatch):
    def handler(req):
        return httpx.Response(404)
    monkeypatch.setattr(main_mod, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup"


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
    assert "demo-v1/SE-RA-1234/manifest.json" not in keys   # no false complete
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys       # partials kept
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify" and "0002" in str(term)


def test_max_pages_caps(env, cfg, s3):
    env = dict(env, MAX_PAGES="2")
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    body = json.loads(s3.get_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json")["Body"].read())
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
                out.write_text("<alto><Layout><Page/></Layout></alto>")  # no WIDTH/HEIGHT
            else:
                out.write_text('<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/>'
                               "</Layout></alto>")
            return {"alto": out}
        return process

    with caplog.at_level("WARNING"):
        rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "viewer manifest covers 2/3 pages" in caplog.text
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/iiif.json" in keys  # still published for the 2 good pages


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
