import json
import os
import signal
import threading
from pathlib import Path

import httpx
import pytest

from htrflow_batch import main as main_mod
from htrflow_batch.iiif import redact_url
from htrflow_batch.main import (
    EXIT_OK,
    EXIT_PERMANENT,
    EXIT_SIGTERM,
    EXIT_TRANSIENT,
    main,
)
from htrflow_batch.store import ResultStore
from htrflow_batch.stream import PageOutcome, StreamStats


@pytest.fixture
def env(tmp_path, cfg, sample_manifest, monkeypatch):
    """Full env + mocked HTTP (manifest + images) + moto S3 via cfg/s3 fixtures."""

    def handler(req):
        if req.url.path.endswith("manifest.json"):
            return httpx.Response(200, json=sample_manifest)
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

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


ALTO_OK = '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
PAGE_OK = '<PcGts><Page imageWidth="2500" imageHeight="3538"/></PcGts>'


def _write_outputs(cfg, stem: str, alto: str = ALTO_OK, page: str = PAGE_OK):
    files = {}
    for fmt, text in (("alto", alto), ("page", page)):
        out = Path(cfg.workdir) / "outputs" / fmt / f"{stem}.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        files[fmt] = out
    return files


def fake_factory(cfg):
    """Writes a plausible ALTO + PAGE per page."""

    def process(path: Path):
        return _write_outputs(cfg, path.stem)

    return process


def _put_done(s3, cfg, name: str, formats=("alto", "page")):
    for fmt in formats:
        s3.put_object(
            Bucket=cfg.s3_bucket,
            Key=f"demo-v1/SE-RA-1234/{fmt}/{name}.xml",
            Body=(ALTO_OK if fmt == "alto" else PAGE_OK).encode(),
        )


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
    # W7: canvas -> source mapping, so a later run can tell a changed page
    assert body["page_sources"] == {
        f"{i:04d}": f"https://iiif.example/mock-vol/page-{i:05d}/full/2500,/0/default.jpg"
        for i in (1, 2, 3)
    }
    assert body["canvas_ids"] == {
        f"{i:04d}": f"https://iiif.example/mock-vol/page-{i:05d}/canvas"
        for i in (1, 2, 3)
    }


def test_resume_skips_done(env, cfg, s3):
    _put_done(s3, cfg, "0001")
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


def test_resume_reprocesses_pages_whose_source_changed(env, cfg, s3):
    """W7: resume was by position only; an edited images: list or a
    re-ordered manifest kept stale outputs. The previous manifest.json's
    page_sources are compared with the current image URLs."""
    for name in ("0001", "0002", "0003"):
        _put_done(s3, cfg, name)
    src = "https://iiif.example/mock-vol/page-{:05d}/full/2500,/0/default.jpg"
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json",
        Body=json.dumps(
            {
                "pages": 3,
                "page_sources": {
                    "0001": src.format(1),
                    "0002": "https://iiif.example/OLD/page-00002/full/2500,/0/default.jpg",
                    "0003": src.format(3),
                },
            }
        ).encode(),
    )
    calls = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            calls.append(path.stem)
            return inner(path)

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    assert calls == ["0002"]
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0001"]["status"] == "skipped"
    assert body["results"]["0002"]["status"] == "ok"
    assert body["page_sources"]["0002"] == src.format(2)


def test_resume_keeps_done_pages_whose_stored_source_is_redacted(images_env, cfg, s3):
    """page_sources is stored redacted (S6), so the comparison must redact
    too. A tokenised private IIIF URL otherwise looked "changed" on every
    retry and the whole volume was reprocessed, forever."""
    url = "https://img.example/1.jpg?token=SECRET"
    _put_done(s3, cfg, "0001")
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json",
        Body=json.dumps(
            {"pages": 1, "page_sources": {"0001": redact_url(url)}}
        ).encode(),
    )
    calls = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            calls.append(path.stem)
            return inner(path)

        return process

    assert main(dict(images_env, IMAGES=url), process_page_factory=factory) == EXIT_OK
    assert calls == []
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0001"]["status"] == "skipped"


def test_resume_without_previous_manifest_keeps_done_pages(env, cfg, s3):
    """No manifest.json (the previous attempt never completed) = nothing to
    compare against; done pages stay done."""
    _put_done(s3, cfg, "0001")
    calls = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            calls.append(path.stem)
            return inner(path)

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    assert calls == ["0002", "0003"]


def test_resume_reprocesses_page_with_alto_but_no_page_xml(env, cfg, s3):
    """W2: a previous run that died between the two uploads must not count
    the page as done."""
    _put_done(s3, cfg, "0001", formats=("alto",))
    calls = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            calls.append(path.stem)
            return inner(path)

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    assert calls == ["0001", "0002", "0003"]
    assert "demo-v1/SE-RA-1234/page/0001.xml" in _keys(s3, cfg)


def test_verify_requires_page_xml_too(env, cfg, s3, monkeypatch):
    real = ResultStore.upload_page

    def drop_page_for_0002(self, name, files):
        if name != "0002":
            return real(self, name, files)
        # simulate a PAGE PUT that never landed, bypassing upload_page's check
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._key(f"alto/{name}.xml"),
            Body=files["alto"].read_bytes(),
        )

    monkeypatch.setattr(ResultStore, "upload_page", drop_page_for_0002)
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify" and "missing=['0002']" in term["error"]
    assert "demo-v1/SE-RA-1234/manifest.json" not in _keys(s3, cfg)


def test_malformed_alto_fails_the_page_at_upload(env, cfg, s3):
    """W3: the page fails in the stream (never uploaded) so the verify gate
    reports it; a later retry reprocesses it instead of accepting the junk."""

    def factory(c):
        def process(path):
            if path.stem == "0002":
                return _write_outputs(c, path.stem, alto="<alto><Layout></alto>")
            return _write_outputs(c, path.stem)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_TRANSIENT
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0002.xml" not in keys
    assert "demo-v1/SE-RA-1234/page/0002.xml" not in keys
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify" and "0002" in term["error"]
    # B63/D7: no failure-evidence object is published any more
    assert "demo-v1/SE-RA-1234/metrics-failed-latest.json" not in keys


def test_verify_failure_reports_why_each_page_failed(env, cfg, s3):
    """A run with failed pages never publishes manifest.json, so the
    termination message is the operator's only record of the cause. It used
    to carry page names alone. URLs inside it are redacted (S6)."""

    def factory(c):
        def process(path):
            if path.stem == "0002":
                raise RuntimeError(
                    "fetch of https://iiif.example/p2?token=SECRET went wrong"
                )
            return _write_outputs(c, path.stem)

        return process

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify"
    assert "0002: " in term["error"] and "went wrong" in term["error"]
    assert "SECRET" not in term["error"]


def test_verify_failure_detail_is_bounded():
    """Every failed page's error in one field would blow past the 3500-char
    cap _terminate truncates at (and the 4 KiB the kubelet keeps): at most 10
    pages, each error clipped, the rest counted."""
    names = [f"{i:04d}" for i in range(1, 51)]
    stats = StreamStats(
        results={n: PageOutcome(status="failed", error="x" * 500) for n in names}
    )
    detail = main_mod._failure_detail(stats, names)
    assert detail.count("x" * 200) == 10 and "x" * 201 not in detail
    assert "0011" not in detail and "(+40 more)" in detail
    assert len(detail) < 3500


class _MissingCachedModel(FileNotFoundError, ValueError):
    """The shape of huggingface_hub.errors.LocalEntryNotFoundError — raised
    under HF_HUB_OFFLINE=1 for a model absent from the read-only cache. Its
    MRO in the wrapper image (huggingface_hub 0.36.2) ends
    ... FileNotFoundError, OSError, ValueError, Exception."""


def test_a_missing_cached_model_is_transient(env, cfg, s3):
    """A bare ValueError from the model factory is a config mistake (exit 13,
    FailIndex). This one is also an OSError: the cache is simply not warm yet,
    and a re-warm plus a retry fixes it — exit 1, not a failed index."""

    def factory(c):
        raise _MissingCachedModel("model 'x' not found in /data/hf")

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["permanent"] is False and term["stage"] == "load"


def test_publish_tolerates_unparseable_previously_uploaded_alto(env, cfg, s3):
    """A resumed page whose stored ALTO cannot be parsed must not fail
    publish: iiif.json simply omits that canvas (with a warning)."""
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/alto/0001.xml",
        Body=b"<alto><Layout></alto>",
    )
    s3.put_object(
        Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/page/0001.xml", Body=b"<PcGts/>"
    )
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    assert {c["id"].rsplit("/", 1)[-1] for c in iiif["items"]} == {"0002", "0003"}


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
    # B63/D7: no failure-evidence object is published any more
    assert "demo-v1/SE-RA-1234/metrics-failed-latest.json" not in keys
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
    """If the downloader dies before it can produce a single page (here the
    dest dir cannot be created), the stream must still terminate instead of
    leaving consume() waiting forever — the pages are then missing, which is
    the verify gate's business."""
    real_mkdir = Path.mkdir

    def boom(self, *a, **k):
        if self.name == "input":
            raise OSError("dest_dir mkdir failed")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", boom)
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
        main_mod.RunState(),
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
            if path.stem == "0002":  # no WIDTH/HEIGHT
                return _write_outputs(
                    c, path.stem, alto="<alto><Layout><Page/></Layout></alto>"
                )
            return _write_outputs(c, path.stem)

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
            # no WIDTH/HEIGHT
            return _write_outputs(
                c, path.stem, alto="<alto><Layout><Page/></Layout></alto>"
            )

        return process

    with caplog.at_level("WARNING"):
        rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    assert "viewer_url will 404" in caplog.text
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/iiif.json" not in keys
    assert "demo-v1/SE-RA-1234/manifest.json" in keys


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


def test_sigterm_writes_termination_log_ships_final_log_and_exits_143(
    env, cfg, s3, monkeypatch
):
    """O2/X5: the Job deadline (or a drain) SIGTERMs the pod. The wrapper
    must leave a termination message naming the stage, ship the final run
    log, and exit 143 promptly — instead of dying with no evidence."""
    exits = []
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: exits.append(code))
    before = signal.getsignal(signal.SIGTERM)

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            if path.stem == "0002":
                os.kill(os.getpid(), signal.SIGTERM)
            return inner(path)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_SIGTERM == 143
    assert exits == [143]
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term == {"stage": "stream", "permanent": False, "error": "SIGTERM"}
    body = (
        s3.get_object(Bucket=cfg.s3_bucket, Key="status/logs/demo-v1/SE-RA-1234.txt")[
            "Body"
        ]
        .read()
        .decode()
    )
    assert "SIGTERM" in body  # final ship carried the shutdown line
    assert "demo-v1/SE-RA-1234/manifest.json" not in _keys(s3, cfg)
    assert signal.getsignal(signal.SIGTERM) is before  # handler restored


def test_sigterm_is_not_swallowed_by_the_per_page_handler(env, cfg, s3, monkeypatch):
    """stream.consume records any Exception as a failed page and carries on;
    the SIGTERM unwind must pass straight through it."""
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: None)
    seen = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            seen.append(path.stem)
            if path.stem == "0001":
                os.kill(os.getpid(), signal.SIGTERM)
            return inner(path)

        return process

    assert main(env, process_page_factory=factory) == EXIT_SIGTERM
    assert seen == ["0001"]  # no further page was processed


def test_store_outage_aborts_in_stream_stage(
    env, cfg, s3, monkeypatch, sample_manifest
):
    def handler(req):
        if req.url.path.endswith("manifest.json"):
            # 9 canvases (page names come from position, ids may repeat)
            m = dict(sample_manifest, items=sample_manifest["items"] * 3)
            return httpx.Response(200, json=m)
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    def dead(self, name, files):
        raise ConnectionError("s3 endpoint unreachable")

    monkeypatch.setattr(ResultStore, "upload_page", dead)
    processed = []

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            processed.append(path.stem)
            return inner(path)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_TRANSIENT
    assert len(processed) == 5  # not all 9
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "stream" and "5 consecutive" in term["error"]


def test_model_load_failure_is_attributed_to_load_stage(env, cfg, s3):
    """W9: a failing model load was reported as stage 'stream'."""

    def factory(c):
        raise OSError("could not reach huggingface.co")

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "load" and term["permanent"] is False


def test_bad_pipeline_config_is_permanent_in_load_stage(env, cfg, s3):
    def factory(c):
        raise ValueError("bad pipeline config: unknown step")

    assert main(env, process_page_factory=factory) == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "load" and term["permanent"] is True


def test_failure_path_stops_the_downloader(env, cfg, s3, monkeypatch):
    """W10: after an early failure the queued downloads must not keep the
    interpreter alive (ThreadPoolExecutor workers are joined at exit)."""
    seen = {}
    real = main_mod.fetched

    def spy(*a, **k):
        seen["stop"] = k.get("stop")
        return real(*a, **k)

    monkeypatch.setattr(main_mod, "fetched", spy)

    def factory(c):
        raise OSError("model load failed")

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    assert seen["stop"] is not None and seen["stop"].is_set()


def test_terminate_redacts_urls_in_the_error(tmp_path):
    """S6: the termination message and run log are world-readable."""
    log_path = tmp_path / "term.log"
    main_mod._terminate(
        {"TERMINATION_LOG_PATH": str(log_path)},
        {
            "stage": "stream",
            "permanent": False,
            "error": "fetch https://user:pw@iiif.example/x/full/max/0/default.jpg"
            "?token=SECRET failed",
        },
        main_mod.RunState(),
    )
    term = json.loads(log_path.read_text())
    assert "SECRET" not in term["error"] and "user:pw" not in term["error"]
    assert "https://iiif.example/x/full/max/0/default.jpg" in term["error"]


def test_manifest_json_page_sources_and_errors_are_redacted(
    env, cfg, s3, monkeypatch, sample_manifest
):
    def handler(req):
        if req.url.path.endswith("manifest.json"):
            m = json.loads(json.dumps(sample_manifest))
            body = m["items"][0]["items"][0]["items"][0]["body"]
            body["service"][0]["id"] = "https://iiif.example/private/p1?token=SECRET"
            return httpx.Response(200, json=m)
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    body = s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
        "Body"
    ].read()
    assert b"SECRET" not in body
    assert json.loads(body)["page_sources"]["0001"].startswith(
        "https://iiif.example/private/p1"
    )


def test_manifest_json_has_no_thumbnail_key(env, cfg, s3):
    """B63/D7: thumbnails were dropped along with the campaign browser."""
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    manifest = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert "thumbnail" not in manifest
    assert "demo-v1/SE-RA-1234/thumb.jpg" not in _keys(s3, cfg)


# -- IMAGES (B63/D6) -------------------------------------------------------


@pytest.fixture
def images_env(tmp_path, cfg, monkeypatch):
    """Like ``env``, but IMAGES replaces IIIF_MANIFEST_URL — no manifest
    fetch, only the two image downloads are mocked."""

    def handler(req):
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0JPEGDATA")

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("steps: []\n")
    return {
        "VOLUME_REF": "SE-RA-1234",
        "IMAGES": "https://img.example/1.jpg,https://img.example/2.jpg",
        "PIPELINE_PATH": str(pipeline),
        "PIPELINE_ID": "demo-v1",
        "S3_ENDPOINT": "",
        "S3_BUCKET": "htr-results",
        "PUBLIC_RESULTS_BASE": "http://public/htr-results",
        "WORKDIR_PATH": str(tmp_path / "work"),
        "TERMINATION_LOG_PATH": str(tmp_path / "term.log"),
    }


def test_images_publishes_the_synthetic_manifest_and_processes_both_pages(
    images_env, cfg, s3
):
    rc = main(images_env, process_page_factory=fake_factory)
    assert rc == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys
    assert "demo-v1/SE-RA-1234/alto/0002.xml" in keys
    src = json.loads(
        s3.get_object(
            Bucket=cfg.s3_bucket, Key="sources/demo-v1/SE-RA-1234/manifest.json"
        )["Body"].read()
    )
    assert src["type"] == "Manifest" and len(src["items"]) == 2
    manifest = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert manifest["pages"] == 2
    assert manifest["source_manifest"] == (
        "http://public/htr-results/sources/demo-v1/SE-RA-1234/manifest.json"
    )


def test_images_honours_s3_prefix_for_the_sources_key(images_env, cfg, s3):
    rc = main(dict(images_env, S3_PREFIX="batch"), process_page_factory=fake_factory)
    assert rc == EXIT_OK
    assert s3.get_object(
        Bucket=cfg.s3_bucket, Key="batch/sources/demo-v1/SE-RA-1234/manifest.json"
    )


def test_images_rejects_a_non_http_url(images_env, cfg, s3):
    env = dict(images_env, IMAGES="not-a-url")
    rc = main(env, process_page_factory=fake_factory)
    assert rc == EXIT_PERMANENT
    term = json.loads(Path(images_env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup" and "http(s)" in term["error"]


# -- MAX_SECONDS (B63/D6) ---------------------------------------------------


class _FakeTimer:
    """Captures the (interval, callback) threading.Timer(...) was built
    with, and whether it was cancelled — no real waiting."""

    instances: list["_FakeTimer"] = []

    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def fake_timer(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    return _FakeTimer


def test_max_seconds_timer_is_started_and_cancelled_on_success(
    env, cfg, s3, fake_timer
):
    """Cheap, deterministic check that the watchdog is wired up and torn down
    on the normal-completion path, without waiting on a real timer."""
    rc = main(dict(env, MAX_SECONDS="5"), process_page_factory=fake_factory)
    assert rc == EXIT_OK
    assert len(fake_timer.instances) == 1
    assert fake_timer.instances[0].interval == 5
    assert fake_timer.instances[0].cancelled is True


def test_max_seconds_fired_after_success_does_not_reverse_the_outcome(
    env, cfg, s3, fake_timer, monkeypatch
):
    """Review finding: timer.cancel() cannot stop a callback that has
    already started, so on_expiry can still fire in the window between a
    successful publish and the finally block's cancel(). Simulate that by
    invoking the captured callback ourselves *after* main() has already
    returned EXIT_OK (same fake-Timer pattern, driven post-hoc instead of
    mid-run) — the shared "terminating" lock must make on_expiry a no-op:
    no termination log, no _hard_exit call."""
    exits = []
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: exits.append(code))

    rc = main(dict(env, MAX_SECONDS="600"), process_page_factory=fake_factory)
    assert rc == EXIT_OK
    assert not Path(env["TERMINATION_LOG_PATH"]).exists()

    fake_timer.instances[0].fn()  # the real Timer thread firing late
    assert exits == []  # on_expiry lost the race and backed off
    assert not Path(env["TERMINATION_LOG_PATH"]).exists()


def test_max_seconds_fired_after_a_permanent_failure_does_not_overwrite_it(
    env, cfg, s3, fake_timer, monkeypatch
):
    """Review finding round 2: the failure paths must go through the same
    `terminating` guard as success — a late on_expiry must not downgrade a
    non-retryable exit 13 (with its own error) to a retryable exit 1."""
    exits = []
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: exits.append(code))

    def factory(c):
        raise ValueError("bad pipeline config: unknown step")

    rc = main(dict(env, MAX_SECONDS="600"), process_page_factory=factory)
    assert rc == EXIT_PERMANENT
    term_before = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term_before["permanent"] is True
    assert "bad pipeline config" in term_before["error"]

    fake_timer.instances[0].fn()  # the real Timer thread firing late
    assert exits == []  # on_expiry lost the race and never called _hard_exit
    term_after = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term_after == term_before  # untouched


def test_max_seconds_fired_after_sigterm_has_started_does_not_overwrite_it(
    env, cfg, s3, fake_timer, monkeypatch
):
    """Same guard, the SIGTERM path this time — the handler in main()."""
    exits = []
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: exits.append(code))

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            if path.stem == "0002":
                os.kill(os.getpid(), signal.SIGTERM)
            return inner(path)

        return process

    rc = main(dict(env, MAX_SECONDS="600"), process_page_factory=factory)
    assert rc == EXIT_SIGTERM
    assert exits == [EXIT_SIGTERM]
    term_before = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term_before == {"stage": "stream", "permanent": False, "error": "SIGTERM"}

    fake_timer.instances[0].fn()  # the real Timer thread firing late
    assert exits == [EXIT_SIGTERM]  # unchanged: on_expiry backed off
    term_after = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term_after == term_before


def test_max_seconds_zero_starts_no_timer(env, cfg, s3, fake_timer):
    rc = main(env, process_page_factory=fake_factory)  # MAX_SECONDS unset (0)
    assert rc == EXIT_OK
    assert fake_timer.instances == []


def test_max_seconds_on_expiry_writes_termination_log_ships_log_and_hard_exits(
    env, cfg, s3, fake_timer, monkeypatch
):
    """Unit-level: fire the watchdog's callback mid-run, exactly as the real
    Timer thread would, and check the three actions the brief specifies —
    termination log, final log ship, hard_exit(1) — without waiting on a
    real timer."""
    exits = []
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: exits.append(code))

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            if path.stem == "0002":
                fake_timer.instances[0].fn()  # fire, as the real Timer would
            return inner(path)

        return process

    rc = main(dict(env, MAX_SECONDS="600"), process_page_factory=factory)
    # on_expiry won the "terminating" race (fired first); the success path,
    # finding the lock already held when it completes, backs off too.
    assert rc == EXIT_TRANSIENT
    assert fake_timer.instances[0].interval == 600
    assert exits == [EXIT_TRANSIENT]
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term == {"stage": "stream", "permanent": False, "error": "MAX_SECONDS"}
    body = (
        s3.get_object(Bucket=cfg.s3_bucket, Key="status/logs/demo-v1/SE-RA-1234.txt")[
            "Body"
        ]
        .read()
        .decode()
    )
    assert "MAX_SECONDS" in body


def test_max_seconds_exceeded_hard_exits_1(env, cfg, s3, monkeypatch):
    """End-to-end with a real (short) threading.Timer and a slow fake driver:
    the watchdog fires in its own thread and force-exits — mocked here so the
    test process itself is not killed — while the main thread is still
    blocked processing a page."""
    exits = []
    hard_exit_called = threading.Event()

    def fake_hard_exit(code):
        exits.append(code)
        hard_exit_called.set()

    monkeypatch.setattr(main_mod, "_hard_exit", fake_hard_exit)
    fired = threading.Event()

    def factory(c):
        def process(path):
            fired.wait(timeout=5)
            return _write_outputs(c, path.stem)

        return process

    env = dict(env, MAX_SECONDS="1")
    real_terminate = main_mod._terminate

    def spy_terminate(e, reason, state):
        won = real_terminate(e, reason, state)
        fired.set()  # let the blocked page finish once the watchdog has run
        return won

    monkeypatch.setattr(main_mod, "_terminate", spy_terminate)
    main(env, process_page_factory=factory)
    assert hard_exit_called.wait(timeout=5)
    assert exits == [EXIT_TRANSIENT]
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term == {"stage": "stream", "permanent": False, "error": "MAX_SECONDS"}
