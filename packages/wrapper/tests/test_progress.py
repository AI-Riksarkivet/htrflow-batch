"""The live progress file and the incremental viewer manifest (C13/C11).

The wrapper is the only writer in the results tree, so "how far has this
volume got" has to leave the pod as an object in the bucket while the run is
still going — these tests pin that it does, that it costs the run nothing
when the bucket refuses it, and that manifest.json is still written last.
"""

import json
import logging
from pathlib import Path

import httpx
import pytest

from htrflow_batch import main as main_mod
from htrflow_batch import progress as progress_mod
from htrflow_batch.logship import LogCapture
from htrflow_batch.main import EXIT_OK, main
from htrflow_batch.progress import LAST_ERROR_CHARS, Progress
from htrflow_batch.store import ResultStore
from htrflow_batch.stream import PageOutcome, StreamStats

ALTO_OK = '<alto><Layout><Page WIDTH="2500" HEIGHT="3538"/></Layout></alto>'
PAGE_OK = '<PcGts><Page imageWidth="2500" imageHeight="3538"/></PcGts>'


def _write_outputs(cfg, stem: str):
    files = {}
    for fmt, text in (("alto", ALTO_OK), ("page", PAGE_OK)):
        out = Path(cfg.workdir) / "outputs" / fmt / f"{stem}.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        files[fmt] = out
    return files


@pytest.fixture
def env(tmp_path, cfg, sample_manifest, monkeypatch):
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


PREFIX = "demo-v1/SE-RA-1234"


def _get(s3, cfg, key: str) -> dict:
    return json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key=f"{PREFIX}/{key}")["Body"].read()
    )


def _factory_watching(cfg, watcher):
    """A page factory that calls ``watcher(stem)`` before it produces its
    outputs — the hook these tests use to look at the bucket mid-run."""

    def factory(c):
        def process(path: Path):
            watcher(path.stem)
            return _write_outputs(cfg, path.stem)

        return process

    return factory


def test_progress_json_is_written_and_says_done_at_the_end(env, cfg, s3):
    assert (
        main(env, process_page_factory=_factory_watching(cfg, lambda _s: None))
        == EXIT_OK
    )
    body = _get(s3, cfg, "progress.json")
    assert body["stage"] == "done"
    assert body["pages_total"] == 3
    assert body["pages_done"] == 3
    assert body["pages_failed"] == 0
    assert body["last_page"] == "0003"
    assert body["started_at"] <= body["updated_at"]


def test_progress_is_readable_while_the_run_is_still_going(env, cfg, s3):
    """The whole point: 137/638 has to be visible before page 638."""
    seen = []

    def watch(stem):
        try:
            seen.append(_get(s3, cfg, "progress.json"))
        except Exception:  # no progress file yet
            seen.append(None)

    assert main(env, process_page_factory=_factory_watching(cfg, watch)) == EXIT_OK
    # Page 1 sees the setup/stream write; page 3 sees page 2's outcome.
    assert seen[0] is not None and seen[0]["pages_done"] == 0
    assert seen[2]["pages_done"] == 2
    assert seen[2]["last_page"] == "0002"
    assert seen[2]["stage"] == "stream"


def test_resumed_pages_count_as_done_from_the_first_write(env, cfg, s3, monkeypatch):
    """A volume resumed at page 600 of 638 must not report 0 done."""
    for fmt, text in (("alto", ALTO_OK), ("page", PAGE_OK)):
        s3.put_object(
            Bucket=cfg.s3_bucket, Key=f"{PREFIX}/{fmt}/0001.xml", Body=text.encode()
        )
    seen = []
    assert (
        main(
            env,
            process_page_factory=_factory_watching(
                cfg, lambda _s: seen.append(_get(s3, cfg, "progress.json"))
            ),
        )
        == EXIT_OK
    )
    assert seen[0]["pages_done"] == 1  # the resumed page, before any new one
    assert _get(s3, cfg, "progress.json")["pages_done"] == 3


def test_the_viewer_manifest_is_published_mid_run(env, cfg, s3, monkeypatch):
    """iiif.json exists with the pages done so far, so the volume opens in
    the viewer before the run finishes."""
    monkeypatch.setattr(progress_mod, "PUBLISH_EVERY_PAGES", 1)
    mid = {}

    def watch(stem):
        if stem == "0003":
            mid["iiif"] = _get(s3, cfg, "iiif.json")
            mid["keys"] = {
                o["Key"]
                for o in s3.list_objects_v2(Bucket=cfg.s3_bucket).get("Contents", [])
            }

    assert main(env, process_page_factory=_factory_watching(cfg, watch)) == EXIT_OK
    assert [c["id"].rsplit("/", 1)[-1] for c in mid["iiif"]["items"]] == [
        "0001",
        "0002",
    ]
    # manifest.json keeps its meaning: the completion marker, written last.
    assert f"{PREFIX}/manifest.json" not in mid["keys"]
    final = _get(s3, cfg, "iiif.json")
    assert len(final["items"]) == 3


def test_a_failing_progress_write_never_fails_the_run(env, cfg, s3, monkeypatch):
    def boom(self, body):
        raise RuntimeError("bucket said no")

    monkeypatch.setattr(ResultStore, "put_progress", boom)
    assert (
        main(env, process_page_factory=_factory_watching(cfg, lambda _s: None))
        == EXIT_OK
    )


def test_counts_come_from_the_stream_stats(cfg, s3):
    """One counter, not two: ok + skipped are done, failed is failed."""
    tracker = Progress(cfg, ResultStore(cfg))
    tracker.stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok"),
            "0002": PageOutcome(status="skipped"),
            "0003": PageOutcome(status="failed", error="boom"),
        }
    )
    body = tracker.body()
    assert (body["pages_done"], body["pages_failed"]) == (2, 1)


def test_progress_json_is_json_at_the_volume_prefix(cfg, s3):
    ResultStore(cfg).put_progress({"stage": "stream"})
    obj = s3.get_object(Bucket=cfg.s3_bucket, Key=f"{PREFIX}/progress.json")
    assert obj["ContentType"] == "application/json"
    assert json.loads(obj["Body"].read()) == {"stage": "stream"}


def _failing_factory(cfg, bad: str):
    def factory(c):
        def process(path: Path):
            if path.stem == bad:
                raise RuntimeError("htrflow's Segmentation worker thread died")
            return _write_outputs(cfg, path.stem)

        return process

    return factory


def test_progress_carries_the_most_recent_page_failure(env, cfg, s3):
    """ "When we have an exception in the log it would be nice to see some
    notice on the front page": the sentence has to leave the pod."""
    main(env, process_page_factory=_failing_factory(cfg, "0002"))
    body = _get(s3, cfg, "progress.json")
    assert body["pages_failed"] == 1
    assert body["last_error"]["page"] == "0002"
    assert "Segmentation worker thread died" in body["last_error"]["error"]
    # log.warning is called for every failed page, so the run has at least one.
    assert body["warnings"] >= 1


def test_the_last_error_is_redacted_and_bounded(cfg, s3):
    tracker = Progress(cfg, ResultStore(cfg))
    tracker.stats = StreamStats(
        results={
            "0001": PageOutcome(status="failed", error="early"),
            "0002": PageOutcome(
                status="failed",
                error="fetch https://iiif.example/p?token=SECRET failed " + "x" * 900,
            ),
        }
    )
    error = tracker.body()["last_error"]
    assert error["page"] == "0002"  # the most recent, not the first
    assert "SECRET" not in error["error"]
    assert len(error["error"]) <= LAST_ERROR_CHARS + 3


def test_no_failure_is_no_last_error(cfg, s3):
    tracker = Progress(cfg, ResultStore(cfg))
    tracker.stats = StreamStats(results={"0001": PageOutcome(status="ok")})
    assert tracker.body()["last_error"] is None
    assert tracker.body()["warnings"] == 0


def test_warnings_are_counted_as_they_are_logged(cfg, s3):
    capture = LogCapture.install()
    try:
        capture.attach_logging()
        tracker = Progress(cfg, ResultStore(cfg), capture)
        assert tracker.body()["warnings"] == 0
        logging.getLogger("htrflow_batch").warning("a page looked odd")
        logging.getLogger("htrflow_batch").info("business as usual")
        logging.getLogger("htrflow_batch").error("worse")
        assert tracker.body()["warnings"] == 2
    finally:
        capture.finish()
