"""Reading a volume's live progress out of the bucket (C13/C11).

The wrapper writes it; this is the only reader. Everything here runs against
an httpx MockTransport — no bucket, no network.
"""

from __future__ import annotations

import gzip
import json
import threading
import tracemalloc

import httpx
import pytest

from htrflow_web import progress as progress_mod
from htrflow_web.progress import (
    ProgressReader,
    _from_manifest,
    _from_progress,
    _quality,
)

BASE = "https://results.example.org/htr-test/demo-v1"

#: The reader computes ageSeconds from its own clock (time.time()), never
#: from the browser's -- fetch() is called with this frozen so the tests
#: stay exact. 12 s after PROGRESS's updated_at.
NOW = 1788859872.0  # 2026-09-08T09:31:12+00:00

PROGRESS = {
    "stage": "stream",
    "pages_total": 638,
    "pages_done": 137,
    "pages_failed": 1,
    "last_page": "0137",
    "started_at": "2026-09-08T09:00:00+00:00",
    "updated_at": "2026-09-08T09:31:00+00:00",
    "last_error": {"page": "0044", "error": "the worker thread died"},
    "errors": 3,
    "viewer_published": True,
}

MANIFEST = {
    "pages": 3,
    "pages_ok": 1,
    "pages_failed": 1,
    "results": {
        "0001": {"status": "ok"},
        "0002": {"status": "skipped"},
        "0003": {"status": "failed"},
    },
}


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    monkeypatch.setattr(progress_mod.time, "time", lambda: NOW)


def reader(routes: dict[str, httpx.Response]) -> tuple[ProgressReader, list[str]]:
    """A reader whose GETs are answered from ``routes``; also returns the log
    of URLs it asked for, which is how the caching tests count requests."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return ProgressReader(httpx.Client(transport=httpx.MockTransport(handler))), asked


def test_a_running_volume_reads_progress_json():
    r, asked = reader(
        {f"{BASE}/vol0/progress.json": httpx.Response(200, json=PROGRESS)}
    )
    assert r.fetch(BASE, "vol0", "active") == {
        "done": 137,
        "total": 638,
        "failed": 1,
        "lastPage": "0137",
        "stage": "stream",
        "updatedAt": "2026-09-08T09:31:00+00:00",
        "ageSeconds": 12,
        "lastError": {"page": "0044", "error": "the worker thread died"},
        "errors": 3,
        "viewerPublished": True,
        "quality": None,
    }
    assert asked == [f"{BASE}/vol0/progress.json"]


def test_a_pending_volume_is_never_fetched():
    """A volume with no pod has written nothing: a GET per pending row of a
    5 000-volume campaign would be the whole cost of this feature."""
    r, asked = reader({})
    assert r.fetch(BASE, "vol0", "pending") is None
    assert asked == []


def test_a_volume_finished_by_an_older_wrapper_falls_back_to_the_manifest():
    r, asked = reader(
        {f"{BASE}/vol0/manifest.json": httpx.Response(200, json=MANIFEST)}
    )
    assert r.fetch(BASE, "vol0", "done") == {
        "done": 2,  # ok + skipped: a skipped page is in the bucket
        "total": 3,
        "failed": 1,
        "lastPage": None,
        "stage": "done",
        "updatedAt": None,
        "ageSeconds": None,
        "lastError": None,
        "errors": 0,
        "viewerPublished": True,
        "quality": None,
    }
    assert asked[-1].endswith("manifest.json")


def test_a_volume_that_completed_with_failed_pages_still_reads_done():
    """A volume completes with its failed pages recorded (the product owner,
    2026-09-14), so manifest.json exists for a volume that lost a page: the
    row is done AND says how many pages it lost, never one without the
    other."""
    r, _ = reader({f"{BASE}/vol0/manifest.json": httpx.Response(200, json=MANIFEST)})
    row = r.fetch(BASE, "vol0", "done")
    assert (row["stage"], row["done"], row["total"], row["failed"]) == ("done", 2, 3, 1)


def test_a_done_volume_prefers_its_progress_file():
    r, asked = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                200, json={**PROGRESS, "stage": "done"}
            ),
            f"{BASE}/vol0/manifest.json": httpx.Response(200, json=MANIFEST),
        }
    )
    assert r.fetch(BASE, "vol0", "done")["stage"] == "done"
    assert asked == [f"{BASE}/vol0/progress.json"]  # the big document is not read


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"pages_total": "many"}),
    ],
)
def test_anything_unreadable_is_no_progress_never_an_error(response):
    r, _ = reader({f"{BASE}/vol0/progress.json": response})
    assert r.fetch(BASE, "vol0", "active") is None


def test_a_transport_failure_is_no_progress():
    def handler(request):
        raise httpx.ConnectError("no route to the bucket")

    r = ProgressReader(httpx.Client(transport=httpx.MockTransport(handler)))
    assert r.fetch(BASE, "vol0", "active") is None


def test_one_get_per_volume_per_window():
    r, asked = reader(
        {f"{BASE}/vol0/progress.json": httpx.Response(200, json=PROGRESS)}
    )
    for _ in range(5):
        r.fetch(BASE, "vol0", "active")
    assert len(asked) == 1


def test_a_last_error_that_is_not_the_shape_we_write_is_dropped():
    """The file is ours, but it is fetched over the network like any other:
    a half-written or hand-edited one must not reach the page as a field the
    frontend's schema then refuses."""
    r, _ = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                200, json={**PROGRESS, "last_error": "boom", "errors": "many"}
            )
        }
    )
    found = r.fetch(BASE, "vol0", "active")
    assert found["lastError"] is None and found["errors"] == 0


def test_an_unparseable_timestamp_is_no_age_rather_than_a_crash():
    r, _ = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                200, json={**PROGRESS, "updated_at": "not a date"}
            )
        }
    )
    found = r.fetch(BASE, "vol0", "active")
    assert found["updatedAt"] == "not a date" and found["ageSeconds"] is None


def test_age_is_never_negative_even_when_the_clock_disagrees():
    """A clock skewed the other way (the wrapper's clock briefly ahead of the
    API's) must not read as a negative age."""
    r, _ = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                200, json={**PROGRESS, "updated_at": "2026-09-08T09:31:20+00:00"}
            )
        }
    )
    assert r.fetch(BASE, "vol0", "active")["ageSeconds"] == 0


def test_a_naive_timestamp_is_read_as_utc_not_local_time():
    """The wrapper always writes tz-aware, but a naive stamp must not be
    shifted by the API pod's zone: it is UTC, like everything else on S3."""
    r, _ = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                200, json={**PROGRESS, "updated_at": "2026-09-08T09:31:00"}
            )
        }
    )
    assert r.fetch(BASE, "vol0", "active")["ageSeconds"] == 12


def test_a_cached_row_ages_with_the_clock(monkeypatch):
    """A done volume's file is cached for the hour because it never changes
    again -- but "updated 8 s ago" does, and the card read it for an hour
    (2026-09-14 audit). The age is recomputed from the cached timestamp on
    every hit; nothing is re-fetched."""
    r, asked = reader(
        {f"{BASE}/vol0/progress.json": httpx.Response(200, json=PROGRESS)}
    )
    first = r.fetch(BASE, "vol0", "done")
    assert first["ageSeconds"] == 12
    monkeypatch.setattr(progress_mod.time, "time", lambda: NOW + 3000)
    again = r.fetch(BASE, "vol0", "done")
    assert asked == [f"{BASE}/vol0/progress.json"], "still one GET"
    assert again["ageSeconds"] == 3012
    assert again["updatedAt"] == first["updatedAt"]


def test_a_row_with_no_timestamp_has_no_age_to_recompute():
    """manifest.json is a completion marker, not a clock."""
    r, _ = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(404),
            f"{BASE}/vol0/manifest.json": httpx.Response(200, json=MANIFEST),
        }
    )
    assert r.fetch(BASE, "vol0", "done")["ageSeconds"] is None
    assert r.fetch(BASE, "vol0", "done")["ageSeconds"] is None


def test_a_body_bigger_than_the_cap_is_no_progress():
    """The file is ours, but it arrives over the network like any other
    document and this process holds it in memory (2026-09-14 audit). A
    bucket serving something enormous at that key is an unreadable file,
    which this module already knows how to answer for."""
    huge = {"pages_total": 1, "pad": "x" * (progress_mod.MAX_BODY + 1)}
    r, _ = reader({f"{BASE}/vol0/progress.json": httpx.Response(200, json=huge)})
    assert r.fetch(BASE, "vol0", "active") is None


def test_a_body_inside_the_cap_still_reads():
    ok = {**PROGRESS, "pad": "x" * 1000}
    r, _ = reader({f"{BASE}/vol0/progress.json": httpx.Response(200, json=ok)})
    assert r.fetch(BASE, "vol0", "active")["total"] == 638


def _gzipped(doc: bytes) -> httpx.Response:
    return httpx.Response(
        200, content=gzip.compress(doc), headers={"content-encoding": "gzip"}
    )


def test_a_compressed_body_is_never_inflated(monkeypatch):
    """Anything that can write to the bucket can store progress.json with
    `Content-Encoding: gzip`, and the cap counted bytes after the client
    had inflated them: 32 KiB on the wire became 32 MiB in the pod before
    the cap looked (2026-09-23 audit). The file is asked for unencoded, and
    one that comes back encoded anyway is not read at all."""
    bomb = json.dumps({"pages_total": 1}).encode() + b" " * (32 * 1024 * 1024)
    r, _ = reader({f"{BASE}/vol0/progress.json": _gzipped(bomb)})
    tracemalloc.start()
    try:
        assert r.fetch(BASE, "vol0", "active") is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, f"inflated to {peak} bytes"


def test_the_file_is_asked_for_unencoded():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept-encoding"))
        return httpx.Response(200, json=PROGRESS)

    r = ProgressReader(httpx.Client(transport=httpx.MockTransport(handler)))
    assert r.fetch(BASE, "vol0", "active")["total"] == 638
    assert seen == ["identity"]


def test_an_encoded_answer_is_unreadable_even_when_small():
    small = json.dumps(PROGRESS).encode()
    r, _ = reader({f"{BASE}/vol0/progress.json": _gzipped(small)})
    assert r.fetch(BASE, "vol0", "active") is None


def test_a_redirect_is_not_followed():
    """The URL is built from an operator's results base; a bucket that
    answers it with a redirect is not somewhere this pod should follow."""
    r, asked = reader(
        {
            f"{BASE}/vol0/progress.json": httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        }
    )
    assert r.fetch(BASE, "vol0", "active") is None
    assert asked == [f"{BASE}/vol0/progress.json"]


def test_the_strings_a_person_reads_are_clipped():
    """`lastPage`, `stage` and the error sentence all render into the card.
    A megabyte of them is not a page the reader can use."""
    long = "y" * 5000
    doc = {
        **PROGRESS,
        "last_page": long,
        "stage": long,
        "last_error": {"page": long, "error": long},
    }
    r, _ = reader({f"{BASE}/vol0/progress.json": httpx.Response(200, json=doc)})
    got = r.fetch(BASE, "vol0", "active")
    assert len(got["lastPage"]) == progress_mod.MAX_FIELD
    assert len(got["stage"]) == progress_mod.MAX_FIELD
    assert len(got["lastError"]["error"]) == progress_mod.MAX_FIELD
    assert len(got["lastError"]["page"]) == progress_mod.MAX_FIELD


def test_a_volume_id_cannot_walk_out_of_its_own_prefix():
    """Volume ids come off a campaign's volumes.txt, a file people edit in a
    git repo. Unencoded, `../..` was normalised by the client into a request
    for somebody else's key (2026-09-14 audit)."""
    r, asked = reader({})
    r.fetch(BASE, "../../status/logs", "active")
    assert asked == [f"{BASE}/..%2F..%2Fstatus%2Flogs/progress.json"]


def test_an_ordinary_volume_id_is_left_as_it_is():
    r, asked = reader({})
    r.fetch(BASE, "R0001203", "active")
    assert asked == [f"{BASE}/R0001203/progress.json"]


# --- what the campaign's totals are read from (3076) ----------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(progress_mod.time, "monotonic", c)
    return c


def test_the_cache_is_asked_without_a_get():
    """The campaign's totals read every run volume from the cache, and only
    a miss may cost a GET -- so asking the cache must never make one."""
    r, asked = reader(
        {f"{BASE}/vol0/progress.json": httpx.Response(200, json=PROGRESS)}
    )
    assert r.cached(BASE, "vol0", "active") == (False, None)
    assert asked == []
    fetched = r.fetch(BASE, "vol0", "active")
    assert r.cached(BASE, "vol0", "active") == (True, fetched)
    assert len(asked) == 1


def test_a_finished_volume_with_no_file_is_an_answer_kept_for_the_hour(clock):
    """A done volume's pod wrote everything it ever will before it exited,
    so a 404 then is final. Kept for the short window it was asked again on
    every poll, and a campaign of such volumes spent the whole fetch cap on
    them, starving the rest of the campaign's totals."""
    r, asked = reader({})
    assert r.fetch(BASE, "vol0", "done") is None
    clock.now += progress_mod.RUNNING_TTL + 1
    assert r.cached(BASE, "vol0", "done") == (True, None)
    r.fetch(BASE, "vol0", "done")
    assert len(asked) == 2, "progress.json and manifest.json, once each"


def test_a_failed_volumes_file_is_final_too(clock):
    r, asked = reader(
        {f"{BASE}/vol0/progress.json": httpx.Response(200, json=PROGRESS)}
    )
    r.fetch(BASE, "vol0", "failed")
    clock.now += progress_mod.RUNNING_TTL + 1
    assert r.cached(BASE, "vol0", "failed")[0] is True
    assert len(asked) == 1


def test_a_bucket_that_did_not_answer_is_not_an_answer(clock):
    r, _ = reader({f"{BASE}/vol0/progress.json": httpx.Response(503)})
    assert r.fetch(BASE, "vol0", "done") is None
    assert r.cached(BASE, "vol0", "done") == (False, None)


def test_a_full_cache_drops_its_oldest_entry_not_everything(monkeypatch):
    """Cleared whole, one campaign bigger than the cache emptied it for
    every other campaign on every poll."""
    monkeypatch.setattr(progress_mod, "MAX_ENTRIES", 3)
    r, _ = reader({})
    for i in range(4):
        r.fetch(BASE, f"vol{i}", "active")
    assert r.cached(BASE, "vol0", "active")[0] is False
    assert all(r.cached(BASE, f"vol{i}", "active")[0] for i in (1, 2, 3))


class _Interleaved(dict):
    """A cache that lets another request run in the middle of an eviction,
    at the one point where two threads of the pool can meet: after this
    request picked the oldest key and before it deleted it."""

    def __init__(self, other) -> None:
        super().__init__()
        self.other = other
        self.thread: threading.Thread | None = None

    def __delitem__(self, key) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self.other)
            self.thread.start()
            self.thread.join(timeout=0.2)  # blocked on the lock, or finished
        super().__delitem__(key)


def test_two_requests_evicting_at_once_do_not_trip_over_each_other(monkeypatch):
    """The reader is shared by every thread of the pool, and a full cache is
    the normal state at scale: two requests evicting the same oldest key
    raised a KeyError -- a 500 for a page whose progress is decoration."""
    monkeypatch.setattr(progress_mod, "MAX_ENTRIES", 2)
    r, _ = reader({})
    errors: list[BaseException] = []

    def other() -> None:
        try:
            r.fetch(BASE, "vol-other", "active")
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    r._cache = _Interleaved(other)
    r.fetch(BASE, "vol0", "active")
    r.fetch(BASE, "vol1", "active")
    r.fetch(BASE, "vol2", "active")  # full: evicts, and lets `other` in
    assert r._cache.thread is not None
    r._cache.thread.join()
    assert errors == []
    assert len(r._cache) == 2


# --- the wrapper's quality block, sanitised (Task 6) -----------------------

GOOD = {
    "target": "bow_f1",
    "model": "org/qp",
    "revision": "a" * 40,
    "mean": 0.8,
    "min": 0.4,
    "scored": 3,
    "lowest": [{"page": "0002", "quality": 0.4, "canvas": 1}],
}


def test_a_quality_block_passes_through():
    assert _quality(GOOD) == {
        "mean": 0.8,
        "min": 0.4,
        "scored": 3,
        "model": "org/qp",
        "revision": "a" * 40,
        "lowest": [{"page": "0002", "quality": 0.4, "canvas": 1}],
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "0.8",
        [],
        {},
        {**GOOD, "mean": 7},
        {**GOOD, "mean": True},
        {**GOOD, "scored": -1},
        {**GOOD, "min": float("nan")},
        {**GOOD, "scored": 10**400},
        {**GOOD, "scored": progress_mod.MAX_SCORED + 1},
    ],
)
def test_a_malformed_quality_block_is_none(bad):
    assert _quality(bad) is None


def test_lowest_is_clipped_and_bad_entries_dropped():
    many = [{"page": f"{i:04d}", "quality": 0.1, "canvas": i} for i in range(10_000)]
    block = _quality({**GOOD, "lowest": [{"page": 3}, *many]})
    assert len(block["lowest"]) == 5
    assert block["lowest"][0]["page"] == "0000"


def test_more_pages_scored_than_the_volume_has_is_no_quality():
    """``scored`` counts pages of this volume: more than it has is not a
    block of ours, and would outweigh every other volume in the campaign's
    mean. Exactly as many is fine."""
    doc = {"pages_total": 2, "pages_done": 2, "quality": GOOD}
    assert _from_progress(doc, 0.0)["quality"] is None
    assert _from_progress({**doc, "pages_total": 3}, 0.0)["quality"] is not None
    manifest = {"pages": 2, "results": {"0001": {"status": "ok"}}, "quality": GOOD}
    assert _from_manifest(manifest, 0.0)["quality"] is None
    assert _from_manifest({**manifest, "pages": 3}, 0.0)["quality"] is not None


def test_a_page_named_twice_in_lowest_is_kept_once():
    """The frontend keys its list by page: a duplicate would throw there.
    The first entry, the lowest the wrapper ranked, is the one kept, and
    the duplicate does not take one of the five places."""
    dup = [
        {"page": "0002", "quality": 0.4, "canvas": 1},
        {"page": "0002", "quality": 0.5, "canvas": 1},
        *({"page": f"{i:04d}", "quality": 0.6, "canvas": i} for i in range(3, 8)),
    ]
    block = _quality({**GOOD, "lowest": dup})
    assert [e["page"] for e in block["lowest"]] == [
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
    ]
    assert block["lowest"][0]["quality"] == 0.4


def test_progress_and_manifest_both_carry_it():
    doc = {"pages_total": 3, "pages_done": 3, "quality": GOOD}
    assert _from_progress(doc, 0.0)["quality"]["mean"] == 0.8
    assert _from_progress({"pages_total": 3}, 0.0)["quality"] is None
    manifest = {"pages": 3, "results": {"0001": {"status": "ok"}}, "quality": GOOD}
    assert _from_manifest(manifest, 0.0)["quality"]["scored"] == 3
