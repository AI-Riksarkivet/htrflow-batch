import threading
import time
from pathlib import Path

import httpx
import pytest

from htrflow_batch import stream as stream_mod
from htrflow_batch.fetch import FetchResult
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import UploadOutage, consume, fetched

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
    """A page stream shaped like ``fetched()``'s: consume() drives it with
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

    stream = fetched(_pages(3), tmp_path / "in", _client(handler), lookahead=64)
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

    stream = fetched(_pages(6), tmp_path, _client(handler), lookahead=2, concurrency=6)
    seen = []
    for item in stream:
        assert len(list(tmp_path.glob("*.jpg"))) <= 2  # never more than the window
        seen.append(item.page.name)
        item.path.unlink()  # what consume()'s finally does
    assert seen == [f"{i:04d}" for i in range(1, 7)]
    assert len(started) == 6
    assert peak[0] <= 2


def test_lookahead_one_downloads_a_single_page_ahead(tmp_path):
    """With lookahead=1 and a consumer that has not come back yet, exactly one
    page downloads; the next goes out only when the consumer asks again."""
    started = []

    def handler(req):
        started.append(req.url.path)
        return httpx.Response(200, content=JPEG)

    stream = fetched(_pages(3), tmp_path, _client(handler), lookahead=1)
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

    stream = fetched(_pages(3), tmp_path, _client(handler), lookahead=3, concurrency=3)
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
        stream = fetched(_pages(3), tmp_path / "in", _client(handler), lookahead=64)
        assert list(stream) == []
    assert "downloader failed" in caplog.text
    assert "mkdir failed" in (stream.error or "")


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

    stream = fetched(
        _pages(40),
        tmp_path,
        _client(handler),
        lookahead=64,
        concurrency=1,
        stop=stop,
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


def test_fetch_failure_recorded(tmp_path):
    closed = []
    stats = consume(
        _items([_fr(tmp_path, 1, fail=True)], closed), lambda p: {}, lambda n, f: None
    )
    assert stats.results["0001"].status == "failed"
    assert "500" in stats.results["0001"].error
    assert closed == [True]  # a failed fetch still ends the stream cleanly


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


def test_keep_images_preserves_file(tmp_path):
    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(
        _items([_fr(tmp_path, 1)]), process, lambda n, f: None, keep_images=True
    )
    assert stats.results["0001"].status == "ok"
    assert (tmp_path / "0001.jpg").exists()  # image preserved


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
    assert stats.results["0009"].status == "failed"


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
