import threading
import time
from pathlib import Path

import httpx
import pytest

from htrflow_batch import stream as stream_mod
from htrflow_batch.fetch import FetchResult
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import PageStream, UploadOutage, consume

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12  # JPEG SOI + APP0 marker


def _pages(n):
    return [
        PageRef(index=i, name=f"{i:04d}", image_url=f"https://img/{i}", canvas={})
        for i in range(1, n + 1)
    ]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _wait_for(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def _fr(tmp_path, i, fail=False):
    page = PageRef(index=i, name=f"{i:04d}", image_url=f"https://img/{i}", canvas={})
    if fail:
        return FetchResult(page=page, path=None, error="HTTP 500")
    p = tmp_path / f"{i:04d}.jpg"
    p.write_bytes(b"jpg")
    return FetchResult(page=page, path=p, error=None, size=3)


def _items(results, closed=None):
    """A page stream shaped like ``PageStream``'s: consume() drives it with
    next(), and its finally records the cleanup an abort must trigger (what
    the Semaphore's release used to stand for)."""

    def gen():
        try:
            yield from results
        finally:
            if closed is not None:
                closed.append(True)

    return gen()


# -- the bounded-lookahead stream ------------------------------------------


def test_fetched_downloads_every_page_and_counts_bytes(tmp_path):
    def handler(req):
        return httpx.Response(200, content=JPEG + req.url.path.encode())

    stream = PageStream(_pages(3), tmp_path / "in", _client(handler), lookahead=64)
    results = list(stream)
    assert [r.page.name for r in results] == ["0001", "0002", "0003"]
    assert all(r.path and r.error is None for r in results)
    assert sorted(p.name for p in (tmp_path / "in").iterdir()) == [
        "0001.jpg",
        "0002.jpg",
        "0003.jpg",
    ]
    assert stream.bytes_fetched == sum(r.size for r in results)


def test_lookahead_bounds_downloads_in_flight_and_images_on_disk(tmp_path):
    """The backpressure the Semaphore gave: with lookahead=2 at most two
    downloads are in flight ahead of the consumer and at most two images sit
    in tmpfs, however wide the pool is."""
    lock = threading.Lock()
    live, peak, started = [], [0], []

    def handler(req):
        with lock:
            started.append(req.url.path)
            live.append(1)
            peak[0] = max(peak[0], len(live))
        time.sleep(0.01)  # widen the window in which pages overlap
        with lock:
            live.pop()
        return httpx.Response(200, content=JPEG)

    stream = PageStream(
        _pages(6), tmp_path, _client(handler), lookahead=2, concurrency=6
    )
    seen = []
    for item in stream:
        assert len(list(tmp_path.glob("*.jpg"))) <= 2  # never more than the window
        seen.append(item.page.name)
        item.path.unlink()  # what consume()'s finally does
    assert seen == [f"{i:04d}" for i in range(1, 7)]
    assert len(started) == 6
    assert peak[0] <= 2


def test_lookahead_is_bounded_by_bytes_as_well_as_pages(tmp_path):
    """Audit 0923 W-9: 64 pages of lookahead at up to FETCH_MAX_BYTES each is
    4 GiB against a 2 Gi memory-backed workdir. A page that has not landed
    holds FETCH_MAX_BYTES of the byte budget, one that has landed its own
    size, so what can sit in the workdir never passes the budget."""
    lock = threading.Lock()
    started = []
    body = JPEG  # 16 bytes

    def handler(req):
        with lock:
            started.append(req.url.path)
        return httpx.Response(200, content=body)

    stream = PageStream(
        _pages(8),
        tmp_path,
        _client(handler),
        lookahead=64,
        concurrency=8,
        max_bytes=20,
        lookahead_bytes=60,
    )
    try:
        # three pages reserve 3 x 20 = 60; a fourth would pass the budget
        _wait_for(lambda: len(started) == 3)
        time.sleep(0.05)
        assert len(started) == 3
        pages = iter(stream)
        first = next(pages)
        first.path.unlink()
        # landed at 16 bytes each: 16 + 16 + 20 (the new one) fits, and so
        # does no more than that
        assert next(pages).page.name == "0002"
        _wait_for(lambda: len(started) == 4)
        rest = [first, *pages]
        assert len(started) == 8 and len(rest) == 7
    finally:
        stream.close()


def test_a_page_larger_than_the_byte_budget_still_downloads(tmp_path):
    """Alone in the window it is allowed through, else the stream would
    stop: one page at a time is what a budget smaller than a page means."""
    stream = PageStream(
        _pages(3),
        tmp_path,
        _client(lambda r: httpx.Response(200, content=JPEG)),
        lookahead=64,
        max_bytes=100,
        lookahead_bytes=50,
    )
    assert [r.page.name for r in stream] == ["0001", "0002", "0003"]


def test_lookahead_one_downloads_a_single_page_ahead(tmp_path):
    """With lookahead=1 and a consumer that has not come back yet, exactly one
    page downloads; the next goes out only when the consumer asks again."""
    started = []

    def handler(req):
        started.append(req.url.path)
        return httpx.Response(200, content=JPEG)

    stream = PageStream(_pages(3), tmp_path, _client(handler), lookahead=1)
    try:
        _wait_for(lambda: started == ["/1"])
        time.sleep(0.05)
        assert started == ["/1"]  # nothing beyond the window
        pages = iter(stream)
        assert next(pages).page.name == "0001"
        assert started == ["/1"]  # still nothing: the slot frees on the next ask
        assert next(pages).page.name == "0002"
        assert started == ["/1", "/2"]
    finally:
        stream.close()


def test_results_arrive_in_submission_order(tmp_path):
    """Pages reach the consumer in manifest order, whatever order they land
    in: page 1 still retrying holds up 2 and 3, exactly as the relay thread
    did — page-first uploads and the run log follow the manifest."""
    release = threading.Event()

    def handler(req):
        if req.url.path == "/1":
            assert release.wait(5), "page 1 was never released"
        return httpx.Response(200, content=JPEG)

    stream = PageStream(
        _pages(3), tmp_path, _client(handler), lookahead=3, concurrency=3
    )
    pages = iter(stream)
    _wait_for(lambda: len(list(tmp_path.glob("*.jpg"))) == 2)  # 2 and 3 landed
    release.set()
    assert [next(pages).page.name for _ in range(3)] == ["0001", "0002", "0003"]
    with pytest.raises(StopIteration):
        next(pages)


def test_a_downloader_failure_terminates_the_stream(tmp_path, monkeypatch, caplog):
    """What the ``None`` sentinel used to guarantee: a downloader that dies
    before it can produce anything (here: the dest dir cannot be created)
    ends the stream instead of leaving the consumer waiting forever. The
    pages are then simply missing, which the verify gate reports."""

    def boom(self, *a, **k):
        raise OSError("dest_dir mkdir failed")

    monkeypatch.setattr(Path, "mkdir", boom)

    def handler(req):
        return httpx.Response(200, content=JPEG)

    with caplog.at_level("ERROR"):
        stream = PageStream(_pages(3), tmp_path / "in", _client(handler), lookahead=64)
        assert list(stream) == []
    assert "downloader failed" in caplog.text
    assert "mkdir failed" in caplog.text


def test_stop_event_short_circuits_pending_downloads(tmp_path):
    """W10: once the run has failed, queued pages must not each spend their
    retries/timeouts before the process can exit. The mock server answers
    the first page at once and holds every later one on a gate the test
    opens after setting ``stop``: with concurrency 1 exactly one more
    request is in flight, and the remaining 38 must be short-circuited
    without ever reaching the server (no wall-clock assertion needed)."""
    stop = threading.Event()
    gate = threading.Event()
    in_flight = threading.Event()  # the second request has reached the server
    started = []

    def handler(req):
        started.append(req.url.path)
        if len(started) > 1:
            in_flight.set()
            assert gate.wait(5), "test never opened the gate"
        return httpx.Response(200, content=JPEG)

    stream = PageStream(
        _pages(40),
        tmp_path,
        _client(handler),
        lookahead=64,
        concurrency=1,
        stop=stop,
        max_bytes=1024,  # all 40 inside the byte budget too, as in the window
    )
    pages = iter(stream)
    first = next(pages)
    assert first.path is not None
    # Only once page 2 is inside the server is "one in flight" a fact; setting
    # stop before that races the worker and short-circuits page 2 as well.
    assert in_flight.wait(5), "second request never reached the server"
    stop.set()
    gate.set()
    rest = list(pages)
    assert len(rest) == 39  # every page after the first is still reported
    assert rest[0].path is not None  # the one the gate held, completed
    assert all(r.path is None and "stopped" in r.error for r in rest[1:])
    assert len(started) == 2  # page 1, and the one already in flight


def _spy_fetch(monkeypatch, raise_for=()):
    """fetch_page as the stream binds it, recording each page it is handed
    (and raising, like a bug in it would, for the names in ``raise_for``)."""
    handed: list[str] = []
    real = stream_mod.fetch_page

    def fetch(page, **kw):
        handed.append(page.name)
        if page.name in raise_for:
            raise RuntimeError(f"bug fetching {page.name}")
        return real(page, **kw)

    monkeypatch.setattr(stream_mod, "fetch_page", fetch)
    return handed


def test_a_set_stop_submits_no_further_page(tmp_path, monkeypatch):
    """W10: once the run has failed nothing more is handed to the download
    pool. The pages behind the window are not reported as "stopped" one by
    one -- they are never submitted, and verify reports them missing."""
    handed = _spy_fetch(monkeypatch)
    stop = threading.Event()
    stream = PageStream(
        _pages(6),
        tmp_path,
        _client(lambda req: httpx.Response(200, content=JPEG)),
        lookahead=2,
        stop=stop,
    )
    pages = iter(stream)
    assert next(pages).page.name == "0001"
    stop.set()
    assert [r.page.name for r in pages] == ["0002"]  # already in the window
    assert handed == ["0001", "0002"]


# -- the consumer ----------------------------------------------------------


def test_ok_flow_uploads_and_deletes(tmp_path):
    closed = []
    uploaded = {}

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(
        _items([_fr(tmp_path, 1)], closed),
        process,
        lambda n, f: uploaded.update({n: f}),
    )
    assert stats.results["0001"].status == "ok"
    assert "0001" in uploaded
    assert not (tmp_path / "0001.jpg").exists()  # rolling cleanup
    assert closed == [True]  # the stream ran to the end, not half-iterated


def test_process_failure_recorded_and_loop_continues(tmp_path):
    def process(path: Path):
        if path.stem == "0001":
            raise RuntimeError("boom")
        out = tmp_path / f"{path.stem}.alto.xml"
        out.write_text("<alto/>")
        return {"alto": out}

    closed = []
    stats = consume(
        _items([_fr(tmp_path, 1), _fr(tmp_path, 2)], closed), process, lambda n, f: None
    )
    assert stats.results["0001"].status == "failed"
    assert "boom" in stats.results["0001"].error
    assert stats.results["0002"].status == "ok"
    assert closed == [True]  # both pages consumed, stream not left half-iterated


def test_a_page_failure_is_a_sentence_not_a_repr(tmp_path):
    """What a failed page records is what the notice chip shows. The
    wrapper's own exceptions are sentences already (PipelineDead); a foreign
    one keeps its type, since "'NoneType' object is not iterable" alone says
    nothing about what threw it. Never repr(): PipelineDead("page 0044: ...")
    reached the status page verbatim on the first live run (2026-09-08)."""
    from htrflow_batch.driver import PipelineDead, _dead

    class Step:
        metadata = None

        def __str__(self):
            return "Segmentation"

    def process(path: Path):
        if path.stem == "0001":
            raise _dead(Step(), "0001")
        if path.stem == "0002":
            raise TypeError("'NoneType' object is not iterable")
        raise PipelineDead("")

    stats = consume(
        _items([_fr(tmp_path, 1), _fr(tmp_path, 2), _fr(tmp_path, 3)], []),
        process,
        lambda n, f: None,
    )
    assert stats.results["0001"].error == (
        "page 0001: htrflow's Segmentation (model unknown) worker thread died; "
        "the page is marked failed and the pipeline is rebuilt"
    )
    assert stats.results["0002"].error == (
        "TypeError: 'NoneType' object is not iterable"
    )
    assert stats.results["0003"].error == "PipelineDead"


def test_fetch_failure_recorded(tmp_path):
    closed = []
    stats = consume(
        _items([_fr(tmp_path, 1, fail=True)], closed), lambda p: {}, lambda n, f: None
    )
    assert stats.results["0001"].status == "failed"
    assert "500" in stats.results["0001"].error
    assert closed == [True]  # a failed fetch still ends the stream cleanly


def test_each_page_failure_is_logged_with_its_cause(tmp_path, caplog):
    """The per-page error used to live only in stats.results, and a run with
    failed pages never publishes manifest.json — so the operator saw
    "verify failed: failed=['0001']" and nothing about why."""

    def process(path: Path):
        raise RuntimeError("CUDA out of memory")

    with caplog.at_level("WARNING"):
        consume(
            _items([_fr(tmp_path, 1, fail=True), _fr(tmp_path, 2)]),
            process,
            lambda n, f: None,
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("0001" in m and "500" in m for m in warnings)
    assert any("0002" in m and "CUDA out of memory" in m for m in warnings)


def test_stall_accounting(tmp_path, monkeypatch):
    """GPU stall = time spent waiting for the next page. A fake clock advances
    while ``next()`` runs, so the number is exact and no thread sleeps."""
    clock = {"now": 100.0}

    def slow_stream():
        clock["now"] += 0.3  # 0.3 s waiting for the page
        yield _fr(tmp_path, 1)
        clock["now"] += 0.1  # 0.1 s waiting for the end of the stream

    monkeypatch.setattr(
        stream_mod.time, "monotonic", lambda: clock["now"], raising=True
    )
    stats = consume(slow_stream(), lambda p: {}, lambda n, f: None)
    assert stats.stall_seconds == pytest.approx(0.4)


def test_keep_images_preserves_the_image_only(tmp_path):
    """The flag is named for what it keeps: the outputs are rolling-deleted
    either way, so debugging a run cannot fill the memory-backed workdir."""
    out = {}

    def process(path: Path):
        out["alto"] = tmp_path / "alto" / f"{path.stem}.xml"
        out["alto"].parent.mkdir(exist_ok=True)
        out["alto"].write_text("<alto/>")
        return dict(out)

    stats = consume(
        _items([_fr(tmp_path, 1)]), process, lambda n, f: None, keep_images=True
    )
    assert stats.results["0001"].status == "ok"
    assert (tmp_path / "0001.jpg").exists()  # image preserved
    assert not out["alto"].exists()  # outputs are not


def _ok_process(tmp_path):
    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    return process


def test_consecutive_upload_failures_abort_the_stream(tmp_path):
    """W6: every upload failing means the store is gone; draining the whole
    volume through a dead bucket is the 6 h zombie."""
    attempts = []
    closed = []

    def upload(name, files):
        attempts.append(name)
        raise ConnectionError("endpoint down")

    with pytest.raises(UploadOutage, match="5 consecutive"):
        consume(
            _items([_fr(tmp_path, i) for i in range(1, 8)], closed),
            _ok_process(tmp_path),
            upload,
            max_upload_failures=5,
        )
    assert attempts == ["0001", "0002", "0003", "0004", "0005"]
    assert closed == [True]  # the abort closes the stream, not left half-iterated


def test_upload_failure_counter_resets_on_success(tmp_path):
    n = {"i": 0}

    def upload(name, files):
        n["i"] += 1
        if n["i"] % 5 == 0:
            return None  # one success in five
        raise ConnectionError("flaky")

    stats = consume(
        _items([_fr(tmp_path, i) for i in range(1, 10)]),
        _ok_process(tmp_path),
        upload,
        max_upload_failures=5,
    )
    assert stats.results["0005"].status == "ok"
    assert stats.results["0009"].status == "deferred"


def test_a_transient_upload_failure_defers_the_page(tmp_path, caplog):
    """Audit 0923 W-1: one PUT that fails after boto's own retries (a network
    blip, a 503 SlowDown) is the store's condition, not the page's. Recorded
    as `failed`, verify counted the page as accounted for, manifest.json was
    written and the page was never retried. Deferred, it is missing: the run
    exits 1 and the index's retry redoes only that page."""
    calls = []

    def upload(name, files):
        calls.append(name)
        if name == "0002":
            raise ConnectionError("SlowDown")

    with caplog.at_level("WARNING"):
        stats = consume(
            _items([_fr(tmp_path, i) for i in range(1, 4)]),
            _ok_process(tmp_path),
            upload,
        )
    assert calls == ["0001", "0002", "0003"]
    assert {n: r.status for n, r in stats.results.items()} == {
        "0001": "ok",
        "0002": "deferred",
        "0003": "ok",
    }
    assert "SlowDown" in (stats.results["0002"].error or "")
    assert "0002 deferred" in caplog.text


def test_page_validation_errors_do_not_count_as_outage(tmp_path):
    """upload_page raises ValueError for the page's own bad output (W2/W3);
    that is not an S3 outage."""

    def upload(name, files):
        raise ValueError("alto XML is not well-formed")

    stats = consume(
        _items([_fr(tmp_path, i) for i in range(1, 8)]),
        _ok_process(tmp_path),
        upload,
        max_upload_failures=5,
    )
    assert len(stats.results) == 7
    assert all(r.status == "failed" for r in stats.results.values())


def test_workdir_holds_only_the_page_in_flight(tmp_path):
    """X2: the workdir is a memory-backed emptyDir, and the page outputs used
    to stay in it for the whole volume (~300 KB/page, 2 Gi near 7 000 pages).
    Both formats must go with the image once the outcome is recorded, so 50
    pages cost exactly what one does."""
    workdir = tmp_path / "work"
    inputs = workdir / "input"
    inputs.mkdir(parents=True)

    def process(path: Path):
        files = {}
        for fmt in ("alto", "page"):
            out = workdir / "outputs" / fmt / f"{path.stem}.xml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("<x/>")
            files[fmt] = out
        return files

    in_flight = []

    def upload(name, files):
        # the outputs are still there while the page is being uploaded
        in_flight.append(sorted(p.name for p in workdir.rglob("*") if p.is_file()))

    # lazily, one page at a time: PageStream bounds the images in flight by
    # its lookahead window (tested above), so what is left to bound is the
    # outputs, and this keeps the count exact.
    consume(_items(_fr(inputs, i) for i in range(1, 51)), process, upload)

    assert in_flight == [
        [f"{i:04d}.jpg", f"{i:04d}.xml", f"{i:04d}.xml"] for i in range(1, 51)
    ]
    assert [p for p in workdir.rglob("*") if p.is_file()] == []


def test_the_download_deadline_reaches_every_fetch(tmp_path, monkeypatch):
    seen = []

    def fake_fetch(page, **kw):
        seen.append(kw["deadline"])
        return FetchResult(page=page, path=None, error="x")

    monkeypatch.setattr(stream_mod, "fetch_page", fake_fetch)
    stream = PageStream(
        _pages(2), tmp_path, _client(lambda r: None), lookahead=4, deadline=7.0
    )
    assert len(list(stream)) == 2 and seen == [7.0, 7.0]


def test_a_transient_fetch_failure_is_deferred_not_failed(tmp_path, caplog):
    """3095: a page the source could not serve today is not an outcome; it
    stays out of `failed` so verify reports it missing and the retry redoes
    it."""
    page = _pages(1)[0]
    item = FetchResult(page=page, path=None, error="HTTP 503", transient=True)
    with caplog.at_level("WARNING"):
        stats = consume(_items([item]), lambda p: {}, lambda n, f: None)
    assert stats.results["0001"].status == "deferred"
    assert stats.results["0001"].error == "HTTP 503"
    assert "deferred" in caplog.text
