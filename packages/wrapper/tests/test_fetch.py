import queue
import threading
from pathlib import Path
from unittest.mock import patch

import httpx

from htrflow_batch.fetch import FetchResult, run_downloader
from htrflow_batch.iiif import PageRef

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12  # JPEG SOI + APP0 marker


def _pages(n):
    return [
        PageRef(index=i, name=f"{i:04d}", image_url=f"https://img/{i}", canvas={})
        for i in range(1, n + 1)
    ]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_downloads_all_pages(tmp_path):
    def handler(req):
        return httpx.Response(200, content=JPEG + req.url.path.encode())

    q, slots = queue.Queue(), threading.Semaphore(64)
    total = run_downloader(_pages(3), tmp_path, q, slots, _client(handler))
    results = [q.get() for _ in range(3)]
    assert q.get() is None  # sentinel
    assert all(isinstance(r, FetchResult) and r.path for r in results)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "0001.jpg",
        "0002.jpg",
        "0003.jpg",
    ]
    assert total == sum(r.size for r in results)


def test_failed_page_reports_error_not_exception(tmp_path):
    def handler(req):
        if req.url.path == "/2":
            return httpx.Response(500)
        return httpx.Response(200, content=JPEG)

    q, slots = queue.Queue(), threading.Semaphore(64)
    run_downloader(
        _pages(3), tmp_path, q, slots, _client(handler), retries=2, backoff=0.0
    )
    results = {r.page.name: r for r in (q.get(), q.get(), q.get())}
    assert q.get() is None
    assert results["0002"].path is None
    assert "500" in results["0002"].error
    assert results["0001"].path and results["0003"].path


def test_lookahead_blocks(tmp_path):
    """With 1 slot and a consumer that never releases, only 1 page downloads."""

    def handler(req):
        return httpx.Response(200, content=JPEG)

    q, slots = queue.Queue(), threading.Semaphore(1)
    t = threading.Thread(
        target=run_downloader,
        args=(_pages(3), tmp_path, q, slots, _client(handler)),
        daemon=True,
    )
    t.start()
    first = q.get(timeout=5)
    assert first.page.name == "0001"
    t.join(timeout=0.5)
    assert t.is_alive()  # blocked waiting for a slot
    slots.release()
    slots.release()
    t.join(timeout=5)
    assert not t.is_alive()


def test_non_httpx_exception_caught(tmp_path):
    """Non-httpx exceptions (e.g. OSError opening the file) are caught and reported."""

    def handler(req):
        return httpx.Response(200, content=JPEG)

    q, slots = queue.Queue(), threading.Semaphore(64)

    # Monkeypatch Path.open to raise OSError for page 2
    original_open = Path.open

    def patched_open(self, *a, **k):
        if self.name == "0002.jpg":
            raise OSError("Disk full")
        return original_open(self, *a, **k)

    with patch.object(Path, "open", patched_open):
        run_downloader(_pages(3), tmp_path, q, slots, _client(handler), retries=1)

    # Collect all results (3 pages + sentinel)
    results = [q.get() for _ in range(3)]
    assert q.get() is None

    # Verify we got exactly 3 FetchResults
    assert len(results) == 3
    assert all(isinstance(r, FetchResult) for r in results)

    # Pages 1 and 3 should succeed
    assert results[0].page.name == "0001"
    assert results[0].path is not None
    assert results[0].error is None

    assert results[2].page.name == "0003"
    assert results[2].path is not None
    assert results[2].error is None

    # Page 2 should have error
    assert results[1].page.name == "0002"
    assert results[1].path is None
    assert "OSError" in results[1].error


def test_upscale_400_falls_back_to_max(tmp_path):
    """lbiiif (IIIF level1) returns 400 for sized requests wider than the
    original image (no upscaling). On 400 the fetcher retries with full/max
    instead of failing the page (R0001203 page 0002, 1281px wide)."""

    def handler(req):
        if "/full/2500,/" in req.url.path:
            return httpx.Response(400)
        if "/full/max/" in req.url.path:
            return httpx.Response(200, content=JPEG + b"narrow-image")
        return httpx.Response(404)

    pages = [
        PageRef(
            index=1,
            name="0001",
            image_url="https://img/iiif/full/2500,/0/default.jpg",
            canvas={},
        )
    ]
    q, slots = queue.Queue(), threading.Semaphore(64)
    run_downloader(pages, tmp_path, q, slots, _client(handler), retries=3, backoff=0.0)
    r = q.get()
    assert q.get() is None
    assert r.error is None
    assert r.path is not None and r.path.read_bytes() == JPEG + b"narrow-image"


def _one(tmp_path, handler, **kw):
    q, slots = queue.Queue(), threading.Semaphore(64)
    run_downloader(_pages(1), tmp_path, q, slots, _client(handler), backoff=0.0, **kw)
    r = q.get()
    assert q.get() is None
    return r


def test_html_200_is_retried_then_failed(tmp_path):
    """W4: a 200 with an HTML body (login page, error page) used to be saved
    as the JPEG and burn a whole attempt in htrflow. It is a failed fetch."""
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<html>login</html>"
        )

    r = _one(tmp_path, handler, retries=2)
    assert r.path is None
    assert "text/html" in r.error
    assert len(calls) == 2  # retryable
    assert list(tmp_path.iterdir()) == []


def test_non_image_bytes_rejected_even_with_image_content_type(tmp_path):
    def handler(req):
        return httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, content=b"<html>nope</html>"
        )

    r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "not an image" in r.error
    assert list(tmp_path.iterdir()) == []


def test_empty_body_rejected(tmp_path):
    def handler(req):
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"")

    r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "empty" in r.error
    assert list(tmp_path.iterdir()) == []


def test_octet_stream_with_image_bytes_accepted(tmp_path):
    """Static hosts (S3 without a content type) serve octet-stream; the
    magic bytes decide."""

    def handler(req):
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=JPEG
        )

    r = _one(tmp_path, handler, retries=1)
    assert r.path is not None and r.error is None


def test_body_over_cap_fails_without_retry_and_leaves_no_file(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=JPEG + b"x" * 1000)

    r = _one(tmp_path, handler, retries=3, max_bytes=100)
    assert r.path is None and "too large" in r.error
    assert len(calls) == 1  # a bigger image tomorrow is not a thing
    assert list(tmp_path.iterdir()) == []


def test_content_length_over_cap_rejected_before_reading(tmp_path):
    def handler(req):
        return httpx.Response(200, headers={"Content-Length": "999999"}, content=b"")

    r = _one(tmp_path, handler, retries=1, max_bytes=100)
    assert r.path is None and "too large" in r.error


def test_partial_file_unlinked_on_write_failure(tmp_path):
    """W5: ENOSPC mid-write must not leave a truncated JPEG for htrflow."""

    def handler(req):
        return httpx.Response(200, content=JPEG + b"y" * 10)

    original_open = Path.open

    class _Broken:
        def __init__(self, f):
            self._f = f

        def write(self, data):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._f.close()

    def patched_open(self, *a, **k):
        f = original_open(self, *a, **k)
        return _Broken(f) if self.name == "0001.jpg" else f

    with patch.object(Path, "open", patched_open):
        r = _one(tmp_path, handler, retries=1)
    assert r.path is None and "No space left" in r.error
    assert not (tmp_path / "0001.jpg").exists()


def test_stop_event_short_circuits_pending_downloads(tmp_path):
    """W10: once the run has failed, queued pages must not each spend their
    retries/timeouts before the process can exit. The mock server answers
    the first page at once and holds every later one on a gate the test
    opens after setting ``stop``: with concurrency 1 exactly one more
    request is in flight, and the remaining 38 must be short-circuited
    without ever reaching the server (no wall-clock assertion needed)."""
    stop = threading.Event()
    gate = threading.Event()
    started = []

    def handler(req):
        started.append(req.url.path)
        if len(started) > 1:
            assert gate.wait(5), "test never opened the gate"
        return httpx.Response(200, content=JPEG)

    q, slots = queue.Queue(), threading.Semaphore(64)
    t = threading.Thread(
        target=run_downloader,
        args=(_pages(40), tmp_path, q, slots, _client(handler)),
        kwargs={"concurrency": 1, "stop": stop},
        daemon=True,
    )
    t.start()
    first = q.get(timeout=5)
    assert first.path is not None
    stop.set()
    gate.set()
    t.join(timeout=5)
    assert not t.is_alive()
    results = []
    while True:
        r = q.get(timeout=1)
        if r is None:
            break
        results.append(r)
    assert len(results) == 39  # every page after the first is still reported
    assert results[0].path is not None  # the one the gate held, completed
    assert all(r.path is None and "stopped" in r.error for r in results[1:])
    assert len(started) == 2  # page 1, and the one already in flight
