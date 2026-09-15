"""Reading a volume's live progress out of the bucket (C13/C11).

The wrapper writes it; this is the only reader. Everything here runs against
an httpx MockTransport — no bucket, no network.
"""

from __future__ import annotations

import httpx
import pytest

from htrflow_web import progress as progress_mod
from htrflow_web.progress import ProgressReader

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
