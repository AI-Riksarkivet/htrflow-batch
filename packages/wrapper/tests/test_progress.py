"""The live progress file and the incremental viewer manifest (C13/C11).

The wrapper is the only writer in the results tree, so "how far has this
volume got" has to leave the pod as an object in the bucket while the run is
still going — these tests pin that it does, that it costs the run nothing
when the bucket refuses it, and that manifest.json is still written last.
"""

import json
import logging
import os
import signal
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


def test_viewer_published_becomes_true_after_the_first_interim_publish(
    env, cfg, s3, monkeypatch
):
    """The frontend's "open" link must switch on this, never on a page count
    (finding 2): before the first interim publish it stays false."""
    monkeypatch.setattr(progress_mod, "PUBLISH_EVERY_PAGES", 1)
    seen = []

    def watch(stem):
        seen.append(_get(s3, cfg, "progress.json")["viewer_published"])

    assert main(env, process_page_factory=_factory_watching(cfg, watch)) == EXIT_OK
    # Watched before each page's own processing: page 1 has not published
    # yet, pages 2 and 3 see page 1's interim publish.
    assert seen == [False, True, True]


def test_viewer_published_stays_false_until_done_for_a_small_volume(env, cfg, s3):
    """A volume with fewer pages than PUBLISH_EVERY_PAGES (10) never crosses
    the interim cadence mid-run -- it must not claim a viewer link that would
    404, and it must still end up true once the run's own final publish
    writes iiif.json."""
    seen = []

    def watch(stem):
        seen.append(_get(s3, cfg, "progress.json")["viewer_published"])

    assert main(env, process_page_factory=_factory_watching(cfg, watch)) == EXIT_OK
    assert seen == [False, False, False]
    assert _get(s3, cfg, "progress.json")["viewer_published"] is True


def test_interim_publish_is_skipped_when_resumed_dims_lag_done(
    env, cfg, s3, monkeypatch
):
    """A volume resumed at page 600 of 638 must not overwrite a complete
    iiif.json with one covering only the pages since resume (finding 3)."""
    monkeypatch.setattr(progress_mod, "PUBLISH_EVERY_PAGES", 1)
    for stem in ("0001", "0002"):
        for fmt, text in (("alto", ALTO_OK), ("page", PAGE_OK)):
            s3.put_object(
                Bucket=cfg.s3_bucket,
                Key=f"{PREFIX}/{fmt}/{stem}.xml",
                Body=text.encode(),
            )
    complete_iiif = {"items": [{"id": "a"}, {"id": "b"}]}
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key=f"{PREFIX}/iiif.json",
        Body=json.dumps(complete_iiif).encode(),
        ContentType="application/json",
    )
    seen = []

    def watch(stem):
        seen.append(_get(s3, cfg, "iiif.json"))

    assert main(env, process_page_factory=_factory_watching(cfg, watch)) == EXIT_OK
    # Page 0003 is the only one this run actually processed: the dims it
    # holds (1) cover fewer pages than `done` (3, two resumed + this one), so
    # the interim publish must not touch the placeholder.
    assert seen == [complete_iiif]
    # The final publish (alto_dims, which reads the resumed pages' ALTO back)
    # still ends up with the complete, correct manifest.
    assert len(_get(s3, cfg, "iiif.json")["items"]) == 3


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


def _failing_factory(cfg, *bad: str):
    def factory(c):
        def process(path: Path):
            if path.stem in bad:
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


def test_a_run_with_a_failed_page_still_ends_at_done(env, cfg, s3):
    """The product owner, 2026-09-14: the page is accounted for, so the volume
    is complete -- the campaign must read "done, 1 failed", not "failed"."""
    assert main(env, process_page_factory=_failing_factory(cfg, "0002")) == 0
    body = _get(s3, cfg, "progress.json")
    assert (body["stage"], body["pages_failed"]) == ("done", 1)


def test_a_failed_run_leaves_a_terminal_stage_not_stuck_at_stream(env, cfg, s3):
    """Before this fix the file stayed at whatever stage the run was doing
    when it stopped -- "stream" forever -- because nothing wrote a last word
    on the failure exit path. main's finally now does. Every page has to fail
    for the run to fail at all now -- one failed page completes the volume."""
    main(env, process_page_factory=_failing_factory(cfg, "0001", "0002", "0003"))
    assert _get(s3, cfg, "progress.json")["stage"] == "failed"


def test_a_sigterm_run_leaves_the_stage_it_was_in(env, cfg, s3, monkeypatch):
    """A SIGTERMed run is the one exception to the terminal stage above (W4):
    the pod has 120 s before the SIGKILL and that time goes to the final log
    ship, not to a status PUT that may sit out its own timeouts first. The
    index is retried anyway, and the termination message -- a local file --
    still names the stage and says SIGTERM."""
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: None)

    def factory(c):
        def process(path: Path):
            if path.stem == "0002":
                os.kill(os.getpid(), signal.SIGTERM)
            return _write_outputs(cfg, path.stem)

        return process

    assert main(env, process_page_factory=factory) == main_mod.EXIT_SIGTERM
    assert _get(s3, cfg, "progress.json")["stage"] == "stream"


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
    assert tracker.body()["errors"] == 0


def test_errors_are_counted_as_they_are_logged(cfg, s3):
    """WARNING does not light the chip -- only ERROR and worse: the wrapper's
    own benign WARNINGs (a pipeline rebuild, "manifest covers n/m") must not
    make every healthy run look like something went wrong."""
    capture = LogCapture.install()
    try:
        capture.attach_logging()
        tracker = Progress(cfg, ResultStore(cfg), capture)
        assert tracker.body()["errors"] == 0
        logging.getLogger("htrflow_batch").warning("a page looked odd")
        logging.getLogger("htrflow_batch").info("business as usual")
        logging.getLogger("htrflow_batch").error("worse")
        assert tracker.body()["errors"] == 1
    finally:
        capture.finish()


def test_sigterm_stops_the_status_writes(env, cfg, s3, monkeypatch):
    """W4: the kubelet gives the pod 120 s between SIGTERM and SIGKILL, and
    the cleanup was spending it on status objects -- this page's progress
    PUT, an interim iiif.json, then one more progress PUT saying "failed" --
    before the final log ship, the one piece of evidence that matters. Once
    the handler has fired, nothing status-shaped is written again."""
    monkeypatch.setattr(progress_mod, "PUBLISH_EVERY_PAGES", 1)
    monkeypatch.setattr(main_mod, "_hard_exit", lambda code: None)
    puts: list[str] = []
    original = ResultStore.put_progress

    def recording(self, body):
        puts.append(body["stage"])
        return original(self, body)

    monkeypatch.setattr(ResultStore, "put_progress", recording)
    at_kill: list[int] = []

    def factory(c):
        def process(path):
            if path.stem == "0002":
                at_kill.append(len(puts))
                os.kill(os.getpid(), signal.SIGTERM)
            return _write_outputs(c, path.stem)

        return process

    assert main(env, process_page_factory=factory) == 143
    assert len(puts) == at_kill[0]  # not one further PUT after the signal
    assert "failed" not in puts


def test_one_unparsable_alto_does_not_disable_the_interim_manifest(
    env, cfg, s3, monkeypatch
):
    """W11: the interim publish is skipped while the dimensions in hand cover
    fewer pages than the run has finished -- a rule meant for a resumed run,
    which reads a page an earlier run uploaded as finished without holding its
    dimensions. A page whose own ALTO will not parse looked exactly the same,
    so a single bad page switched the live viewer off for the rest of the
    volume."""
    monkeypatch.setattr(progress_mod, "PUBLISH_EVERY_PAGES", 1)
    no_dims = "<alto><Layout><Page/></Layout></alto>"

    def factory(c):
        def process(path: Path):
            files = _write_outputs(cfg, path.stem)
            if path.stem == "0001":
                files["alto"].write_text(no_dims)
            return files

        return process

    seen: list = []

    def watch(stem):
        seen.append(_keys_have_iiif(s3, cfg))

    def watching_factory(c):
        inner = factory(c)

        def process(path: Path):
            watch(path.stem)
            return inner(path)

        return process

    assert main(env, process_page_factory=watching_factory) == EXIT_OK
    # Nothing before page 0001; by page 0003 the two finished pages are
    # covered (0002's dimensions plus 0001, known to have none) and the
    # interim manifest is live again.
    assert seen == [False, False, True]
    assert len(_get(s3, cfg, "iiif.json")["items"]) == 2  # 0001 has no canvas


def _keys_have_iiif(s3, cfg) -> bool:
    resp = s3.list_objects_v2(Bucket=cfg.s3_bucket, Prefix=f"{PREFIX}/iiif.json")
    return bool(resp.get("Contents"))
