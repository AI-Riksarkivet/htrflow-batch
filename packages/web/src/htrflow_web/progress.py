"""How far each volume has got, read back out of the results bucket.

Everything else this API answers with comes from the Kubernetes API, which
knows a volume is `active` and nothing more — "page 137 of 638" lives in the
pod, and the wrapper puts it in the bucket as `progress.json`
(docs: reference/s3-layout). This module is the only reader of it.

The cost is bounded by what the response carries, not by the campaign: one
GET per volume row the API is about to answer with, memoized for a few
seconds so a page of 200 rows polled by ten browsers costs 200 GETs per
window rather than 2 000 -- and further capped by the caller
(projection._attach_progress: at most PROGRESS_FETCH_CAP rows, running ones
first) so a `limit=1000` request cannot turn into a thousand sequential
fetches through this one client. Anything unreadable — no file yet, a bucket
that does not answer, a body that is not the object we wrote — is *no
progress*, never an error: this is a decoration on a campaign page that has
to keep working when the bucket does not.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

import httpx

#: A running volume rewrites its file after every page, so a few seconds of
#: staleness is at most a page or two; a finished one never changes again.
RUNNING_TTL = 5.0
DONE_TTL = 3600.0

#: Bounded so a long-lived process browsing a large archive cannot grow this
#: without limit; the whole thing goes rather than evicting cleverly, since a
#: refill is one cheap GET per row on screen.
MAX_ENTRIES = 5000

#: Short: a slow bucket must not hold the API's own response open.
TIMEOUT = 2.0


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _last_error(value: object) -> dict | None:
    """The wrapper's ``{page, error}``, or nothing. The file is ours, but it
    arrives over the network like any other document: a half-written or
    hand-edited one must not reach the page as a field of the wrong shape."""
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if not isinstance(error, str):
        return None
    return {"page": _str_or_none(value.get("page")), "error": error}


def _age_seconds(updated_at: str | None, now: float) -> int | None:
    """Seconds since ``updated_at``, on the API's own clock (``now``, from
    ``time.time()`` at fetch time) — never the browser's. A page open on a
    laptop with a wrong clock must not read "0 s ago" (or a negative age)
    just because its own idea of "now" disagrees with the server's; the
    frontend renders this number as-is, it never computes its own."""
    if updated_at is None:
        return None
    try:
        then = datetime.fromisoformat(updated_at).timestamp()
    except ValueError:
        return None
    return max(0, round(now - then))


def _from_progress(doc: dict, now: float) -> dict | None:
    """The wrapper's ``progress.json``. A document without a page total is
    not one of ours (or is half-written): no progress rather than zeroes."""
    if not isinstance(doc.get("pages_total"), int):
        return None
    updated_at = _str_or_none(doc.get("updated_at"))
    return {
        "done": _int(doc.get("pages_done")),
        "total": _int(doc.get("pages_total")),
        "failed": _int(doc.get("pages_failed")),
        "lastPage": _str_or_none(doc.get("last_page")),
        "stage": _str_or_none(doc.get("stage")),
        "updatedAt": updated_at,
        "ageSeconds": _age_seconds(updated_at, now),
        "lastError": _last_error(doc.get("last_error")),
        "errors": _int(doc.get("errors")),
        # True only once the wrapper's own iiif.json PUT has succeeded
        # (interim or final) — the frontend's "open in the viewer" link
        # switches on this, never on a page count (a volume under
        # PUBLISH_EVERY_PAGES pages would otherwise link to a manifest that
        # is not there yet).
        "viewerPublished": bool(doc.get("viewer_published")),
    }


def _from_manifest(doc: dict, now: float) -> dict | None:
    """The same counts off ``manifest.json``, for a volume finished before
    the wrapper wrote progress files at all. A skipped page is one an earlier
    run produced: it is in the bucket, so it counts as done."""
    del now  # manifest.json carries no timestamp of its own; see below
    results = doc.get("results")
    if not isinstance(doc.get("pages"), int) or not isinstance(results, dict):
        return None
    statuses = [r.get("status") for r in results.values() if isinstance(r, dict)]
    return {
        "done": sum(1 for s in statuses if s in ("ok", "skipped")),
        "total": _int(doc.get("pages")),
        "failed": sum(1 for s in statuses if s == "failed"),
        # manifest.json is the completion marker, not a clock: it carries no
        # page name and no wall-clock timestamp of its own.
        "lastPage": None,
        "stage": "done",
        "updatedAt": None,
        "ageSeconds": None,
        # A volume only publishes manifest.json after a clean verify, so a
        # run this old has no failed page to name and no error count kept.
        "lastError": None,
        "errors": 0,
        # manifest.json existing at all means the viewer manifest does too
        # (publish.py writes iiif.json before it, when any page's dims
        # resolved) -- true is the honest answer for a finished volume.
        "viewerPublished": True,
    }


class ProgressReader:
    """One HTTP client and one small cache for the life of the app."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def fetch(self, results_base: str, volume_id: str, state: str) -> dict | None:
        """This volume's progress, or ``None``. ``results_base`` is the row's
        own (``<public_results_base>/<namespace>/<pipeline>``)."""
        if state == "pending":
            return None  # no pod has run: there is nothing in the bucket yet
        # One clock read per call, not per cache miss: a cached hit still
        # carries the ageSeconds computed when it was fetched (stale by at
        # most the cache TTL), which is the same staleness budget every other
        # field in a cached row already has.
        now = time.time()
        base = f"{results_base}/{volume_id}"
        found = self._cached(f"{base}/progress.json", _from_progress, state, now)
        if found is None and state == "done":
            # Written by a wrapper that predates progress.json. One GET more,
            # cached for the hour: a finished volume is finished.
            found = self._cached(f"{base}/manifest.json", _from_manifest, state, now)
        return found

    def _cached(
        self,
        url: str,
        parse: Callable[[dict, float], dict | None],
        state: str,
        now: float,
    ) -> dict | None:
        monotonic_now = time.monotonic()
        hit = self._cache.get(url)
        if hit is not None and hit[0] > monotonic_now:
            return hit[1]
        value = self._get(url, parse, now)
        # A miss on a done volume is cached briefly, not for the hour: it may
        # simply be a file that has not landed yet.
        ttl = DONE_TTL if state == "done" and value is not None else RUNNING_TTL
        if len(self._cache) >= MAX_ENTRIES:
            self._cache.clear()
        self._cache[url] = (monotonic_now + ttl, value)
        return value

    def _get(
        self, url: str, parse: Callable[[dict, float], dict | None], now: float
    ) -> dict | None:
        try:
            response = self._client.get(url)
            if response.status_code != 200:
                return None
            doc = response.json()
        except Exception:
            return None  # unreachable, timed out, or not JSON at all
        return parse(doc, now) if isinstance(doc, dict) else None
