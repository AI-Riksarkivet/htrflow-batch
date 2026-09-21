import json
import os
import signal
import sys
import threading
from pathlib import Path

import httpx
import pytest

from htrflow_batch import main as main_mod
from htrflow_batch.iiif import redact_url, source_digest
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


def test_default_factory_stamps_provenance_into_each_alto(cfg, monkeypatch):
    """The real factory (htrflow behind it) stamps the ALTO after Export and
    before the caller uploads it; PAGE XML is left alone."""
    from htrflow_batch import driver

    monkeypatch.setattr(driver, "load_pipeline", lambda path, out_dir: "pipeline")
    alto = (
        '<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Description>'
        "<MeasurementUnit>pixel</MeasurementUnit></Description>"
        '<Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
    )
    monkeypatch.setattr(
        driver,
        "process_page",
        lambda pipeline, image_path, out_dir: _write_outputs(
            cfg, image_path.stem, alto=alto
        ),
    )
    cfg = cfg.model_copy(
        update={
            "image_digest": "docker.io/x@sha256:abc",
            "htrflow_base_revision": "v0.2.6-35f48a7",
        }
    )
    files = main_mod._default_factory(cfg)(Path("/img/0001.jpg"))
    alto = files["alto"].read_text()
    assert 'ID="htrflow-batch"' in alto
    assert "image=docker.io/x@sha256:abc" in alto
    assert "htrflow-base=v0.2.6-35f48a7" in alto
    assert "htrflow-batch" not in files["page"].read_text()


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


def _move_source(manifest: dict, to: str) -> None:
    """Point every canvas of the sample manifest at a new image service."""
    for canvas in manifest["items"]:
        body = canvas["items"][0]["items"][0]["body"]
        page = body["service"][0]["id"].rsplit("/", 1)[-1]
        body["service"][0]["id"] = f"https://iiif.example/{to}/{page}"
        body["id"] = f"https://iiif.example/{to}/{page}/full/max/0/default.jpg"


def _attempt(env, calls: list, stop_at: str | None = None) -> int:
    """One run of the volume: records the pages it processes, and dies
    (exit 1, like a SIGTERM or a lost node) on reaching ``stop_at``."""

    def factory(c):
        inner = fake_factory(c)

        def process(path):
            if path.stem == stop_at:
                raise main_mod.Unrecoverable("the node went away")
            calls.append(path.stem)
            return inner(path)

        return process

    return main(env, process_page_factory=factory)


def test_resume_after_a_changed_source_keeps_what_the_last_attempt_did(
    env, cfg, s3, sample_manifest
):
    """3096: the changed-source comparison read only manifest.json, which is
    written only by a COMPLETED run -- so after the source changed, every
    attempt threw away the pages the attempt before it had reprocessed from
    the new source, and a volume that needs more than one attempt never
    completed. Each page now carries the digest of the source it was made
    from, so attempt C keeps attempt B's page."""
    assert _attempt(env, []) == EXIT_OK  # A: complete, from the old source
    _move_source(sample_manifest, "NEW")

    b: list = []
    assert _attempt(env, b, stop_at="0002") == EXIT_TRANSIENT
    assert b == ["0001"]
    # B deleted what it was about to redo: nothing from the old source is
    # left standing for a page it did not reach
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0001.xml" in keys
    assert "demo-v1/SE-RA-1234/alto/0002.xml" not in keys

    c: list = []
    assert _attempt(env, c) == EXIT_OK
    assert c == ["0002", "0003"]
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0001"]["status"] == "skipped"
    assert {body["results"][n]["status"] for n in ("0002", "0003")} == {"ok"}


def test_a_page_made_from_another_source_than_the_manifest_says_is_redone(
    env, cfg, s3, sample_manifest
):
    """The page's own record wins over manifest.json's: a source changed and
    changed back leaves manifest.json agreeing with the current source while
    page 0001 was last made from the other one."""
    assert _attempt(env, []) == EXIT_OK
    _move_source(sample_manifest, "NEW")
    assert _attempt(env, [], stop_at="0002") == EXIT_TRANSIENT
    _move_source(sample_manifest, "mock-vol")

    calls: list = []
    assert _attempt(env, calls) == EXIT_OK
    assert calls == ["0001", "0002", "0003"]


def test_every_page_output_records_its_source(env, cfg, s3):
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    src = "https://iiif.example/mock-vol/page-00001/full/2500,/0/default.jpg"
    for fmt in ("alto", "page"):
        head = s3.head_object(
            Bucket=cfg.s3_bucket, Key=f"demo-v1/SE-RA-1234/{fmt}/0001.xml"
        )
        assert head["Metadata"] == {"source-digest": source_digest(src)}


def test_a_page_half_uploaded_with_resume_off_is_not_done_on_the_retry(
    env, cfg, s3, monkeypatch
):
    """3096: with RESUME=false nothing was deleted first, so a page whose new
    PAGE XML landed and whose ALTO PUT then failed kept the previous run's
    ALTO beside it -- and the retry counted that mixed pair as done. Every
    page a run is about to redo loses its stored outputs first."""
    for name in ("0001", "0002", "0003"):
        _put_done(s3, cfg, name)
    real = ResultStore._put

    def put(self, key, *args, **kwargs):
        if key.endswith("alto/0002.xml"):
            raise RuntimeError("S3 went away mid-page")
        return real(self, key, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(ResultStore, "_put", put)
        assert main({**env, "RESUME": "false"}, process_page_factory=fake_factory) == (
            EXIT_OK
        )
    assert "demo-v1/SE-RA-1234/alto/0002.xml" not in _keys(s3, cfg)

    calls: list = []
    assert _attempt(env, calls) == EXIT_OK
    assert calls == ["0002"]


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

    def drop_page_for_0002(self, name, files, source=None):
        if name != "0002":
            return real(self, name, files, source)
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
    """W3: the page fails in the stream and nothing of it is uploaded -- junk
    is never accepted. The volume still completes, with the page recorded as
    failed in manifest.json (the product owner, 2026-09-14)."""

    def factory(c):
        def process(path):
            if path.stem == "0002":
                return _write_outputs(c, path.stem, alto="<alto><Layout></alto>")
            return _write_outputs(c, path.stem)

        return process

    rc = main(env, process_page_factory=factory)
    assert rc == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0002.xml" not in keys
    assert "demo-v1/SE-RA-1234/page/0002.xml" not in keys
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0002"]["status"] == "failed"
    # B63/D7: no failure-evidence object is published any more
    assert "demo-v1/SE-RA-1234/metrics-failed-latest.json" not in keys


def test_a_failed_page_completes_the_volume(env, cfg, s3):
    """The product owner, 2026-09-14: a volume is complete when every page is
    accounted for. A page that fails deterministically is accounted for --
    recorded as failed -- so the run publishes and exits 0 instead of burning
    the index's four retries on a page that fails identically every time."""

    def factory(c):
        def process(path):
            if path.stem == "0002":
                raise RuntimeError("htrflow's Segmentation worker thread died")
            return _write_outputs(c, path.stem)

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/manifest.json" in keys
    assert "demo-v1/SE-RA-1234/iiif.json" in keys
    # The viewer manifest covers the pages that came out: one canvas short.
    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    assert len(iiif["items"]) == 2
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0002"]["status"] == "failed"
    assert "worker thread died" in body["results"]["0002"]["error"]
    assert not Path(env["TERMINATION_LOG_PATH"]).exists()


def test_a_failed_page_is_named_with_its_cause_in_the_run_log(env, cfg, s3, caplog):
    """Verify no longer raises for a failed page, so its sentence has to reach
    the run log instead -- it is now the operator's record of the cause."""

    def factory(c):
        def process(path):
            if path.stem == "0002":
                raise RuntimeError("dead thread")
            return _write_outputs(c, path.stem)

        return process

    with caplog.at_level("WARNING"):
        assert main(env, process_page_factory=factory) == EXIT_OK
    verify = [
        r.getMessage() for r in caplog.records if "recorded as failed" in r.getMessage()
    ]
    assert verify and "0002: " in verify[0] and "dead thread" in verify[0]


def test_every_processed_page_failing_is_still_a_volume_failure(env, cfg, s3):
    """The guard: a broken model or a dead GPU must not produce an "all pages
    failed, done" volume, so a run that processed pages and got nothing out of
    any of them is transient -- exit 1, retried."""

    def factory(c):
        def process(path):
            raise RuntimeError("CUDA error: no kernel image is available")

        return process

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    assert "demo-v1/SE-RA-1234/manifest.json" not in _keys(s3, cfg)
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify"
    assert term["error"].startswith("verify failed: all 3 processed pages failed")


def test_the_all_failed_guard_does_not_fire_on_a_resumed_run(env, cfg, s3):
    """The guard is for a broken model or a dead GPU, and a resume is neither:
    a volume SIGTERMed at page 637 of 638 whose one remaining page is the dead
    one would otherwise exit 1, retry, and end FailIndex -- the very outcome
    this change removes. Something resumed means the volume is coming out."""
    _put_done(s3, cfg, "0001")
    _put_done(s3, cfg, "0002")

    def factory(c):
        def process(path):
            raise RuntimeError("htrflow's Segmentation worker thread died")

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert (body["pages_ok"], body["pages_failed"]) == (0, 1)
    assert body["results"]["0001"]["status"] == "skipped"


def test_a_missing_page_is_still_a_verify_failure(env, cfg, s3, monkeypatch):
    """A page that is neither in the bucket nor recorded as failed is an
    inconsistency, not an outcome: that stays transient."""
    real = ResultStore.upload_page

    def drop_0002(self, name, files, source=None):
        if name != "0002":
            return real(self, name, files, source)

    monkeypatch.setattr(ResultStore, "upload_page", drop_0002)
    assert main(env, process_page_factory=fake_factory) == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify"
    assert (
        "1 missing, 0 failed" in term["error"] and "missing=['0002']" in term["error"]
    )


def test_the_all_failed_message_reports_why_each_page_failed(env, cfg, s3):
    """A run where every page failed never publishes manifest.json, so the
    termination message is the operator's only record of the cause. It used
    to carry page names alone. URLs inside it are redacted (S6)."""

    def factory(c):
        def process(path):
            raise RuntimeError(
                "fetch of https://iiif.example/p2?token=SECRET went wrong"
            )

        return process

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "verify"
    assert "0002: " in term["error"] and "went wrong" in term["error"]
    assert "SECRET" not in term["error"]


def test_verify_failure_detail_is_bounded():
    """Every failed page's error in one field would blow past the 3500-char
    cap terminate truncates at (and the 4 KiB the kubelet keeps): at most 10
    pages, each error clipped, the rest counted."""
    names = [f"{i:04d}" for i in range(1, 51)]
    stats = StreamStats(
        results={n: PageOutcome(status="failed", error="x" * 500) for n in names}
    )
    detail = main_mod._failure_detail(stats, names)
    assert detail.count("x" * 200) == 10 and "x" * 201 not in detail
    assert "0011" not in detail and "(+40 more)" in detail
    assert len(detail) < 3500


def test_verify_detail_survives_the_termination_log_truncation(tmp_path):
    """terminate clips the error field at 3500 chars, and a page name costs
    ~8 of them, so a volume with 500 missing pages overflows it on the names
    alone. The cause has to be written before them — otherwise the operator
    gets a truncated name list and no reason."""
    from htrflow_batch.iiif import PageRef

    class _NothingUploaded:
        def uploaded_pages(self):
            return set()

    pages = [
        PageRef(index=i, name=f"{i:04d}", image_url="https://x/p.jpg", canvas={})
        for i in range(1, 501)
    ]
    stats = StreamStats(
        results={"0001": PageOutcome(status="failed", error="boom: disk full")}
    )
    with pytest.raises(RuntimeError) as ei:
        main_mod._verify(_NothingUploaded(), pages, stats, main_mod.RunState())
    assert len(str(ei.value)) > 3500  # the names really do overflow the cap

    log_path = tmp_path / "term.log"
    main_mod.terminate(
        {"TERMINATION_LOG_PATH": str(log_path)},
        {"stage": "verify", "permanent": False, "error": str(ei.value)},
    )
    term = json.loads(log_path.read_text())
    assert term["error"].endswith("...(truncated)")
    # 0001 is recorded as failed, so it is accounted for and not missing
    assert "499 missing, 1 failed" in term["error"]
    assert "0001: boom: disk full" in term["error"]


class _MissingCachedModel0x(FileNotFoundError, ValueError):
    """huggingface_hub.errors.LocalEntryNotFoundError as the 0.x line defines
    it — raised under HF_HUB_OFFLINE=1 for a model absent from the read-only
    cache, and a ValueError as well as an OSError."""


class _MissingCachedModel1x(FileNotFoundError):
    """The same error on the 1.x line, which the image carries when it is
    built on the newer transformers line: still an OSError, no longer a
    ValueError."""


@pytest.mark.parametrize("shape", [_MissingCachedModel0x, _MissingCachedModel1x])
def test_a_missing_cached_model_is_transient(env, cfg, s3, shape):
    """A bare ValueError from the model factory is a config mistake (exit 13,
    FailIndex). This one is an OSError, whichever hub line named it: the cache
    is simply not warm yet, and a re-warm plus a retry fixes it — exit 1, not
    a failed index. The classification must not depend on the MRO, which the
    two hub lines disagree about."""
    assert issubclass(shape, OSError)

    def factory(c):
        raise shape("model 'x' not found in /data/hf")

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


@pytest.mark.parametrize(
    "permanent,error,sentence",
    [
        (
            False,
            "verify failed: 2 missing, 0 failed missing=['0002']",
            "some pages produced no result; the retry redoes only those",
        ),
        (
            False,
            "verify failed: all 3 processed pages failed failed=['0001']",
            "no page produced a result — check the model and the GPU",
        ),
        (
            False,
            "SIGTERM",
            "stopped by the cluster (drain, pause, or time budget); retried",
        ),
        (
            True,
            "manifest is not JSON",
            "a retry changes nothing — fix the campaign or pipeline file",
        ),
        (
            False,
            "connection reset",
            "the index is retried, resuming from the pages already done",
        ),
    ],
)
def test_every_failure_line_ends_in_plain_language(permanent, error, sentence):
    """B63 Task 20G: the machine-readable prefix stays (the run viewer's
    terminal-line rule and the read API key on it); what follows the em dash
    is for whoever opened the log."""
    assert main_mod._advice(permanent, error) == sentence


def test_a_missing_env_is_reported_as_the_config_stage(env, cfg, s3):
    """A bad env is a deployment fault, not a manifest one. The stage is the
    only structured signal that says so -- without it the campaign page can
    only guess from the error text, and told the reader to go and fix a
    manifest URL for a missing bucket name (B63 Task 20G fix round 1)."""
    del env["VOLUME_REF"]
    assert main(env, process_page_factory=fake_factory) == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "config"
    assert term["permanent"] is True
    assert "VOLUME_REF" in term["error"]


def test_a_manifest_failure_is_still_the_setup_stage(env, cfg, s3, monkeypatch):
    """The counterpart: `config` must not swallow what follows it."""
    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        ),
    )
    assert main(env, process_page_factory=fake_factory) == EXIT_PERMANENT
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["stage"] == "setup"


def test_a_permanent_failure_line_keeps_its_prefix_and_gains_a_sentence(
    env, cfg, s3, monkeypatch, caplog
):
    """The prefix `permanent failure in <stage>:` is a contract with the
    frontend's terminal-line regex (frontend/src/lib/runlog.ts); the sentence
    after it is new, and neither may push the other out."""

    def handler(req):
        return httpx.Response(404)

    monkeypatch.setattr(
        main_mod,
        "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with caplog.at_level("ERROR"):
        assert main(env, process_page_factory=fake_factory) == EXIT_PERMANENT
    line = next(m for m in caplog.messages if "failure in" in m)
    assert line.startswith("permanent failure in setup:")
    assert line.endswith(
        "— a retry changes nothing — fix the campaign or pipeline file"
    )
    # ...and the machine-readable record is untouched.
    term = json.loads(Path(env["TERMINATION_LOG_PATH"]).read_text())
    assert term["permanent"] is True and "—" not in term["error"]


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

    monkeypatch.setattr(ResultStore, "stored_pages", boom)
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

    main_mod.terminate(
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
    real = main_mod.PageStream

    def spy(*a, **k):
        seen["stop"] = k.get("stop")
        return real(*a, **k)

    monkeypatch.setattr(main_mod, "PageStream", spy)

    def factory(c):
        raise OSError("model load failed")

    assert main(env, process_page_factory=factory) == EXIT_TRANSIENT
    assert seen["stop"] is not None and seen["stop"].is_set()


def test_terminate_redacts_urls_in_the_error(tmp_path):
    """S6: the termination message and run log are world-readable."""
    log_path = tmp_path / "term.log"
    main_mod.terminate(
        {"TERMINATION_LOG_PATH": str(log_path)},
        {
            "stage": "stream",
            "permanent": False,
            "error": "fetch https://user:pw@iiif.example/x/full/max/0/default.jpg"
            "?token=SECRET failed",
        },
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


def _count_alto_reads(monkeypatch) -> list[str]:
    """Every ALTO body publish reads back out of S3."""
    reads: list[str] = []
    original = ResultStore.get_bytes

    def counting(self, rel_key: str):
        if rel_key.startswith("alto/"):
            reads.append(rel_key)
        return original(self, rel_key)

    monkeypatch.setattr(ResultStore, "get_bytes", counting)
    return reads


def test_publish_reads_no_alto_back_for_the_pages_this_run_uploaded(
    env, cfg, s3, monkeypatch
):
    """The rolling delete leaves no local ALTO, so publish would otherwise GET
    every page back for its WIDTH/HEIGHT -- 2 000 sequential round-trips on a
    2 000-page volume, fetching full ALTO bodies for two numbers upload_page
    had already parsed."""
    reads = _count_alto_reads(monkeypatch)

    assert main(env, process_page_factory=fake_factory) == EXIT_OK

    assert reads == []
    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    assert len(iiif["items"]) == 3  # every page still in the viewer manifest
    assert iiif["items"][0]["width"] == 2500


def test_publish_still_reads_back_only_the_pages_a_previous_run_did(
    env, cfg, s3, monkeypatch
):
    """The fallback stays for exactly the pages this run skipped."""
    _put_done(s3, cfg, "0001")
    reads = _count_alto_reads(monkeypatch)

    assert main(env, process_page_factory=fake_factory) == EXIT_OK

    assert reads == ["alto/0001.xml"]


ALTO_STAMPABLE = (
    '<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Description>'
    "<MeasurementUnit>pixel</MeasurementUnit></Description>"
    '<Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
)


def test_default_factory_rebuilds_the_pipeline_after_a_dead_worker_thread(
    cfg, tmp_path, monkeypatch
):
    """B88: the page whose model killed htrflow's worker thread is recorded
    failed with the sentence that names step and model, and the NEXT page is
    processed by a pipeline built from scratch -- the run goes on instead of
    the pod standing still with a reserved GPU until its deadline."""
    from types import ModuleType, SimpleNamespace

    from htrflow_batch import driver
    from htrflow_batch.fetch import FetchResult
    from htrflow_batch.iiif import PageRef
    from htrflow_batch.stream import consume

    fake_steps = ModuleType("htrflow.pipeline.steps")
    fake_steps.auto_import = lambda paths: list(paths)  # the page's own path
    monkeypatch.setitem(sys.modules, "htrflow", ModuleType("htrflow"))
    monkeypatch.setitem(sys.modules, "htrflow.pipeline", ModuleType("htrflow.pipeline"))
    monkeypatch.setitem(sys.modules, "htrflow.pipeline.steps", fake_steps)
    monkeypatch.setattr(driver, "THREAD_POLL_SECONDS", 0.01)
    blocked = threading.Event()

    class _Pipeline:
        """One Inference step whose worker thread dies on page 0002 and whose
        run() then never returns, exactly as htrflow's does."""

        class Segmentation:  # __str__ is the class name, as htrflow's is
            def __init__(self, thread):
                self._thread = thread
                self.model = object()  # the weights on the GPU
                self.metadata = SimpleNamespace(settings={"model": "yolov9-regions-1"})

            def __str__(self):
                return type(self).__name__

        def __init__(self):
            self.thread = SimpleNamespace(alive=True)
            self.thread.is_alive = lambda: self.thread.alive
            self.steps = [self.Segmentation(self.thread)]

        def run(self, document):
            if Path(document).stem == "0002":
                self.thread.alive = False
                blocked.wait(30)
            _write_outputs(cfg, Path(document).stem, alto=ALTO_STAMPABLE)

    built = []

    held = []

    def load_pipeline(path, out_dir):
        # what the dead pipeline still holds when the new one is built: the
        # models must be gone BEFORE a second set is loaded onto the same GPU
        held.append(built[0].steps[0].model if built else "first build")
        built.append(_Pipeline())
        return built[-1]

    monkeypatch.setattr(driver, "load_pipeline", load_pipeline)

    images = []
    for i in (1, 2, 3):
        image = tmp_path / f"{i:04d}.jpg"
        image.write_bytes(b"jpg")
        images.append(
            FetchResult(
                page=PageRef(index=i, name=f"{i:04d}", image_url="x", canvas={}),
                path=image,
                error=None,
            )
        )
    try:
        stats = consume(
            images, main_mod._default_factory(cfg), lambda name, files: None
        )
    finally:
        blocked.set()

    assert [r.status for r in stats.results.values()] == ["ok", "failed", "ok"]
    assert stats.results["0002"].error == (
        "page 0002: htrflow's Segmentation (model yolov9-regions-1) worker "
        "thread died; the page is marked failed and the pipeline is rebuilt"
    )
    assert len(built) == 2  # page 0003 ran on a pipeline built from scratch
    assert held == ["first build", None]  # its weights were dropped first


def test_a_reprocessed_page_that_fails_publishes_no_stale_alto(env, cfg, s3):
    """W3: `missing` subtracts a LIVE S3 listing, so a page reprocessed and
    failed this run used to be 'accounted for' by the previous run's objects
    -- and publish read that stale ALTO into iiif.json while manifest.json
    said the page had failed. The objects go before the page is reprocessed,
    and a failed page never contributes a canvas."""
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

    def factory(c):
        def process(path):
            if path.stem == "0002":
                raise RuntimeError("htrflow's Segmentation worker thread died")
            return _write_outputs(c, path.stem)

        return process

    assert main(env, process_page_factory=factory) == EXIT_OK
    keys = _keys(s3, cfg)
    assert "demo-v1/SE-RA-1234/alto/0002.xml" not in keys
    assert "demo-v1/SE-RA-1234/page/0002.xml" not in keys
    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    assert [c["id"].rsplit("/", 1)[-1] for c in iiif["items"]] == ["0001", "0003"]
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["results"]["0002"]["status"] == "failed"


def test_a_failed_page_keeps_its_stale_alto_out_of_the_viewer(env, cfg, s3):
    """RESUME=off reprocesses every page without a `changed` set to delete
    from, so the failed names are taken out of the uploaded listing publish
    reads dimensions back from (W3)."""
    for name in ("0001", "0002", "0003"):
        _put_done(s3, cfg, name)

    def factory(c):
        def process(path):
            if path.stem == "0002":
                raise RuntimeError("htrflow's Segmentation worker thread died")
            return _write_outputs(c, path.stem)

        return process

    assert main({**env, "RESUME": "false"}, process_page_factory=factory) == EXIT_OK
    iiif = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/iiif.json")[
            "Body"
        ].read()
    )
    assert [c["id"].rsplit("/", 1)[-1] for c in iiif["items"]] == ["0001", "0003"]


def test_resume_reprocesses_a_page_selected_by_its_query(images_env, cfg, s3):
    """W5: hosts that select the image with `?id=` exist, and the stored
    source was redacted (query dropped) before it was compared -- so every
    page of such a manifest looked unchanged, forever. The digest beside it
    keeps the query."""
    _put_done(s3, cfg, "0001")
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json",
        Body=json.dumps(
            {
                "pages": 1,
                "page_sources": {"0001": "https://img.example/iiif"},
                "page_source_digests": {
                    "0001": source_digest("https://img.example/iiif?id=OLD")
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

    env = dict(images_env, IMAGES="https://img.example/iiif?id=NEW")
    assert main(env, process_page_factory=factory) == EXIT_OK
    assert calls == ["0001"]
    body = json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key="demo-v1/SE-RA-1234/manifest.json")[
            "Body"
        ].read()
    )
    assert body["page_source_digests"]["0001"] == source_digest(
        "https://img.example/iiif?id=NEW"
    )


def test_resume_keeps_a_done_page_whose_token_rotated(images_env, cfg, s3):
    """The credentials are taken out of the digest, so a re-signed URL is not
    a new source -- else the whole volume would be reprocessed on every
    retry (W5)."""
    _put_done(s3, cfg, "0001")
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/manifest.json",
        Body=json.dumps(
            {
                "pages": 1,
                "page_source_digests": {
                    "0001": source_digest("https://img.example/1.jpg?token=OLD")
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

    env = dict(images_env, IMAGES="https://img.example/1.jpg?token=NEW")
    assert main(env, process_page_factory=factory) == EXIT_OK
    assert calls == []


def test_a_transient_failure_exits_without_joining_the_downloads(
    env, cfg, s3, monkeypatch, hard_exits
):
    """W7: only SIGTERM went through `_hard_exit`. Exits 1 and 13 returned
    normally, and the interpreter then joined the download pool's workers at
    shutdown -- a fetch sitting in its 120 s timeout held the container open
    long after the run had decided to fail."""
    real = ResultStore.upload_page

    def drop_0002(self, name, files, source=None):
        if name != "0002":
            return real(self, name, files, source)

    monkeypatch.setattr(ResultStore, "upload_page", drop_0002)
    assert main(env, process_page_factory=fake_factory) == EXIT_TRANSIENT
    assert hard_exits == [EXIT_TRANSIENT]


def test_a_permanent_failure_exits_the_same_way(env, cfg, s3, hard_exits):
    assert main({**env, "PIPELINE_PATH": ""}, process_page_factory=fake_factory) == (
        EXIT_PERMANENT
    )
    assert hard_exits == [EXIT_PERMANENT]


def test_a_successful_run_returns_normally(env, cfg, s3, hard_exits):
    """Nothing is in flight when the stream has been consumed to the end, so
    a run that worked still exits the ordinary way."""
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    assert hard_exits == []


def _dead_pipeline_pages(cfg, tmp_path, monkeypatch, rebuilds: list, pages: int):
    """A pipeline that kills its worker thread on every page, with the
    rebuilds after it scripted: ``True`` builds, ``False`` raises. Returns the
    fetched pages to hand to ``consume``."""
    from htrflow_batch import driver
    from htrflow_batch.fetch import FetchResult
    from htrflow_batch.iiif import PageRef

    built = []

    def load_pipeline(path, out_dir):
        ok = rebuilds[len(built)]
        built.append(ok)
        if not ok:
            raise OSError("CUDA error: out of memory")
        return object()

    def process_page(pipeline, image_path, out_dir):
        raise driver.PipelineDead(f"page {image_path.stem}: worker thread died")

    monkeypatch.setattr(driver, "load_pipeline", load_pipeline)
    monkeypatch.setattr(driver, "process_page", process_page)
    monkeypatch.setattr(driver, "release_pipeline", lambda pipeline: None)
    items = []
    for i in range(1, pages + 1):
        image = tmp_path / f"{i:04d}.jpg"
        image.write_bytes(b"jpg")
        items.append(
            FetchResult(
                page=PageRef(index=i, name=f"{i:04d}", image_url="x", canvas={}),
                path=image,
                error=None,
            )
        )
    return items


def test_rebuilds_that_keep_failing_abort_the_run(cfg, tmp_path, monkeypatch):
    """W9: a rebuild that cannot succeed degraded silently -- every later page
    failed, and because the first page or two had come out `ok` the all-failed
    guard never fired: 600 failed pages, manifest.json published, the index
    green. Three consecutive rebuild failures end the run instead, transient,
    so the retry gets a fresh pod."""
    from htrflow_batch.stream import Unrecoverable, consume

    items = _dead_pipeline_pages(
        cfg, tmp_path, monkeypatch, [True, False, False, False], 4
    )
    stats = StreamStats()
    with pytest.raises(Unrecoverable, match="3 consecutive pipeline rebuilds failed"):
        consume(
            items, main_mod._default_factory(cfg), lambda name, files: None, stats=stats
        )
    assert [r.status for r in stats.results.values()] == ["failed"] * 3


def test_a_rebuild_that_works_starts_the_count_again(cfg, tmp_path, monkeypatch):
    """Only CONSECUTIVE failures mean the pipeline cannot be rebuilt at all;
    a rebuild that works says the process is still healthy."""
    from htrflow_batch.stream import Unrecoverable, consume

    rebuilds = [True, False, False, True, False, False, False]
    items = _dead_pipeline_pages(cfg, tmp_path, monkeypatch, rebuilds, 7)
    stats = StreamStats()
    with pytest.raises(Unrecoverable):
        consume(
            items, main_mod._default_factory(cfg), lambda name, files: None, stats=stats
        )
    assert len(stats.results) == 6  # the run reached page 7 before it gave up


def _recording_client(monkeypatch) -> list:
    made: list = []
    original = main_mod._http_client

    def make():
        client = original()
        made.append(client)
        return client

    monkeypatch.setattr(main_mod, "_http_client", make)
    return made


def test_the_http_client_is_closed_on_the_way_out(env, cfg, s3, monkeypatch):
    """W15: the client owns a connection pool and its sockets, and nothing
    ever closed it -- it survived to interpreter shutdown, holding keep-alive
    connections to the image host open for the rest of the run."""
    made = _recording_client(monkeypatch)
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    assert [client.is_closed for client in made] == [True]


def test_the_http_client_is_closed_when_the_run_fails(env, cfg, s3, monkeypatch):
    made = _recording_client(monkeypatch)
    assert main(env, process_page_factory=_failing_everywhere) == EXIT_TRANSIENT
    assert [client.is_closed for client in made] == [True]


def _failing_everywhere(cfg):
    def process(path):
        raise RuntimeError("htrflow's Segmentation worker thread died")

    return process


def test_the_sigterm_handler_outlives_the_final_log_ship(env, cfg, s3, monkeypatch):
    """W16: the handler was put back to the default before `capture.finish()`,
    which is where the run log is uploaded -- so a second SIGTERM arriving
    during a drain (the kubelet's, then the node's) killed the pod outright
    and lost the log the first one had gone to the trouble of preserving."""
    from htrflow_batch.logship import LogCapture

    seen = []
    original = LogCapture.finish

    def finish(self):
        seen.append(signal.getsignal(signal.SIGTERM))
        return original(self)

    monkeypatch.setattr(LogCapture, "finish", finish)
    before = signal.getsignal(signal.SIGTERM)

    assert main(env, process_page_factory=fake_factory) == EXIT_OK

    assert seen == [signal.SIG_IGN]  # uninterruptible for the whole ship
    assert signal.getsignal(signal.SIGTERM) is before  # and put back after it


def test_a_second_sigterm_during_the_final_ship_is_ignored(env, cfg, s3, monkeypatch):
    """W16 review: a drain sends SIGTERM and the node may send another. With
    the handler still installed, the second one raised Terminated inside
    main's own finally -- a traceback out of main, and the streams never put
    back. The cleanup is uninterruptible instead."""
    from htrflow_batch.logship import LogCapture

    original = LogCapture.finish

    def finish(self):
        os.kill(os.getpid(), signal.SIGTERM)  # the node's second signal
        return original(self)

    monkeypatch.setattr(LogCapture, "finish", finish)
    before = signal.getsignal(signal.SIGTERM)
    streams = (sys.stdout, sys.stderr)

    assert main(env, process_page_factory=fake_factory) == EXIT_OK

    assert (sys.stdout, sys.stderr) == streams
    assert signal.getsignal(signal.SIGTERM) is before
