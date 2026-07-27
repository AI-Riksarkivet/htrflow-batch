import queue
import threading
from pathlib import Path

import httpx
from htrflow_batch.fetch import FetchResult, run_downloader
from htrflow_batch.iiif import PageRef


def _pages(n):
    return [PageRef(i, f"{i:04d}", f"https://img/{i}", {}) for i in range(1, n + 1)]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_downloads_all_pages(tmp_path):
    def handler(req):
        return httpx.Response(200, content=b"JPEG" + req.url.path.encode())
    q, slots = queue.Queue(), threading.Semaphore(64)
    total = run_downloader(_pages(3), tmp_path, q, slots, _client(handler))
    results = [q.get() for _ in range(3)]
    assert q.get() is None                      # sentinel
    assert all(isinstance(r, FetchResult) and r.path for r in results)
    assert sorted(p.name for p in tmp_path.iterdir()) == \
        ["0001.jpg", "0002.jpg", "0003.jpg"]
    assert total == sum(r.size for r in results)


def test_failed_page_reports_error_not_exception(tmp_path):
    def handler(req):
        if req.url.path == "/2":
            return httpx.Response(500)
        return httpx.Response(200, content=b"ok")
    q, slots = queue.Queue(), threading.Semaphore(64)
    run_downloader(_pages(3), tmp_path, q, slots, _client(handler),
                   retries=2, backoff=0.0)
    results = {r.page.name: r for r in (q.get(), q.get(), q.get())}
    assert q.get() is None
    assert results["0002"].path is None
    assert "500" in results["0002"].error
    assert results["0001"].path and results["0003"].path


def test_lookahead_blocks(tmp_path):
    """With 1 slot and a consumer that never releases, only 1 page downloads."""
    def handler(req):
        return httpx.Response(200, content=b"ok")
    q, slots = queue.Queue(), threading.Semaphore(1)
    t = threading.Thread(
        target=run_downloader,
        args=(_pages(3), tmp_path, q, slots, _client(handler)),
        daemon=True)
    t.start()
    first = q.get(timeout=5)
    assert first.page.name == "0001"
    t.join(timeout=0.5)
    assert t.is_alive()          # blocked waiting for a slot
    slots.release(); slots.release()
    t.join(timeout=5)
    assert not t.is_alive()
