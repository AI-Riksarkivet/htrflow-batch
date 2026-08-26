import queue
import threading
import time
from pathlib import Path

import pytest

from htrflow_batch.fetch import FetchResult
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import UploadOutage, consume


def _fr(tmp_path, i, fail=False):
    page = PageRef(index=i, name=f"{i:04d}", image_url=f"https://img/{i}", canvas={})
    if fail:
        return FetchResult(page=page, path=None, error="HTTP 500")
    p = tmp_path / f"{i:04d}.jpg"
    p.write_bytes(b"jpg")
    return FetchResult(page=page, path=p, error=None, size=3)


def test_ok_flow_uploads_and_deletes(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1))
    q.put(None)
    uploaded = {}

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: uploaded.update({n: f}))
    assert stats.results["0001"].status == "ok"
    assert "0001" in uploaded
    assert not (tmp_path / "0001.jpg").exists()  # rolling cleanup
    assert slots._value == 1  # released


def test_process_failure_recorded_and_loop_continues(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1))
    q.put(_fr(tmp_path, 2))
    q.put(None)

    def process(path: Path):
        if path.stem == "0001":
            raise RuntimeError("boom")
        out = tmp_path / f"{path.stem}.alto.xml"
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "boom" in stats.results["0001"].error
    assert stats.results["0002"].status == "ok"
    assert slots._value == 2  # 2 releases for 2 pages


def test_fetch_failure_recorded(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1, fail=True))
    q.put(None)
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "500" in stats.results["0001"].error
    assert slots._value == 1  # released


def test_stall_accounting(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)

    def feed():
        time.sleep(0.3)
        q.put(_fr(tmp_path, 1))
        q.put(None)

    threading.Thread(target=feed, daemon=True).start()
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.stall_seconds >= 0.25


def test_keep_images_preserves_file(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1))
    q.put(None)

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: None, keep_images=True)
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
    q, slots = queue.Queue(), threading.Semaphore(0)
    for i in range(1, 8):
        q.put(_fr(tmp_path, i))
    q.put(None)
    attempts = []

    def upload(name, files):
        attempts.append(name)
        raise ConnectionError("endpoint down")

    with pytest.raises(UploadOutage, match="5 consecutive"):
        consume(q, slots, _ok_process(tmp_path), upload, max_upload_failures=5)
    assert attempts == ["0001", "0002", "0003", "0004", "0005"]
    assert slots._value == 5  # every consumed page released its slot


def test_upload_failure_counter_resets_on_success(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    for i in range(1, 10):
        q.put(_fr(tmp_path, i))
    q.put(None)
    n = {"i": 0}

    def upload(name, files):
        n["i"] += 1
        if n["i"] % 5 == 0:
            return None  # one success in five
        raise ConnectionError("flaky")

    stats = consume(q, slots, _ok_process(tmp_path), upload, max_upload_failures=5)
    assert stats.results["0005"].status == "ok"
    assert stats.results["0009"].status == "failed"


def test_page_validation_errors_do_not_count_as_outage(tmp_path):
    """upload_page raises ValueError for the page's own bad output (W2/W3);
    that is not an S3 outage."""
    q, slots = queue.Queue(), threading.Semaphore(0)
    for i in range(1, 8):
        q.put(_fr(tmp_path, i))
    q.put(None)

    def upload(name, files):
        raise ValueError("alto XML is not well-formed")

    stats = consume(q, slots, _ok_process(tmp_path), upload, max_upload_failures=5)
    assert len(stats.results) == 7
    assert all(r.status == "failed" for r in stats.results.values())
