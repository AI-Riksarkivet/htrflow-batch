import queue
import threading
import time
from pathlib import Path

from htrflow_batch.fetch import FetchResult
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import consume


def _fr(tmp_path, i, fail=False):
    page = PageRef(i, f"{i:04d}", f"https://img/{i}", {})
    if fail:
        return FetchResult(page, None, "HTTP 500")
    p = tmp_path / f"{i:04d}.jpg"
    p.write_bytes(b"jpg")
    return FetchResult(page, p, None, 3)


def test_ok_flow_uploads_and_deletes(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1)); q.put(None)
    uploaded = {}

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: uploaded.update({n: f}))
    assert stats.results["0001"].status == "ok"
    assert "0001" in uploaded
    assert not (tmp_path / "0001.jpg").exists()   # rolling cleanup
    assert slots._value == 1                       # released


def test_process_failure_recorded_and_loop_continues(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1)); q.put(_fr(tmp_path, 2)); q.put(None)

    def process(path: Path):
        if path.stem == "0001":
            raise RuntimeError("boom")
        out = tmp_path / f"{path.stem}.alto.xml"; out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "boom" in stats.results["0001"].error
    assert stats.results["0002"].status == "ok"
    assert slots._value == 2                       # 2 releases for 2 pages


def test_fetch_failure_recorded(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1, fail=True)); q.put(None)
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.results["0001"].status == "failed"
    assert "500" in stats.results["0001"].error
    assert slots._value == 1                       # released


def test_stall_accounting(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)

    def feed():
        time.sleep(0.3)
        q.put(_fr(tmp_path, 1)); q.put(None)

    threading.Thread(target=feed, daemon=True).start()
    stats = consume(q, slots, lambda p: {}, lambda n, f: None)
    assert stats.stall_seconds >= 0.25


def test_keep_images_preserves_file(tmp_path):
    q, slots = queue.Queue(), threading.Semaphore(0)
    q.put(_fr(tmp_path, 1)); q.put(None)

    def process(path: Path):
        out = tmp_path / "alto" / f"{path.stem}.xml"
        out.parent.mkdir(exist_ok=True)
        out.write_text("<alto/>")
        return {"alto": out}

    stats = consume(q, slots, process, lambda n, f: None, keep_images=True)
    assert stats.results["0001"].status == "ok"
    assert (tmp_path / "0001.jpg").exists()   # image preserved
