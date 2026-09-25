"""How far each volume has got, read back out of the results bucket.

Everything else this API answers with comes from the Kubernetes API, which
knows a volume is `active` and nothing more — "page 137 of 638" lives in the
pod, and the wrapper puts it in the bucket as `progress.json`
(docs: reference/s3-layout). This module is the only reader of it.

The cost is bounded per request, not by the campaign: every answer is
memoized -- a few seconds for a running volume, the hour for a finished one,
whose file never changes again -- and the caller (projection._read_progress)
makes at most PROGRESS_FETCH_CAP GETs per request for what the cache does
not hold, running volumes first, so a campaign's totals fill in over a few
polls instead of costing a GET per volume on every one. Anything
unreadable — no file yet, a bucket that does not answer, a body that is not
the object we wrote — is *no progress*, never an error: this is a
decoration on a campaign page that has to keep working when the bucket does
not.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

import httpx

#: A running volume rewrites its file after every page, so a few seconds of
#: staleness is at most a page or two; a finished one never changes again.
RUNNING_TTL = 5.0
DONE_TTL = 3600.0

#: Bounded so a long-lived process browsing a large archive cannot grow this
#: without limit: an entry is under 1 KB, so this is ~20 MB of the pod's
#: memory. It is also the largest campaign whose page totals can be every
#: volume's at once (3076) -- past it the oldest answers go first, and the
#: page says how many volumes its totals cover.
MAX_ENTRIES = 20_000

#: States whose file will not change again: the volume is over, or its
#: campaign's Job is gone and nothing is left to write one (`unknown`).
_FINISHED = ("done", "unknown")
#: ...and so whose answer is kept for the hour, an absent file included: a
#: failed index is final too, and its pod wrote what it ever will.
_OVER = (*_FINISHED, "failed")

#: Short: a slow bucket must not hold the API's own response open.
TIMEOUT = 2.0

#: The file is ours, but it arrives over the network like any other
#: document and this process holds it in memory while it parses it. A real
#: progress.json is a few hundred bytes; anything past this is not one, and
#: the body is dropped unread rather than buffered (2026-09-14 audit).
MAX_BODY = 64 * 1024

#: How much of a string from that document may reach the card. `lastPage`,
#: `stage` and the error sentence are all rendered for a person to read.
MAX_FIELD = 300


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str_or_none(value: object) -> str | None:
    """A string from the document, clipped to what a card can show."""
    return value[:MAX_FIELD] if isinstance(value, str) else None


def _last_error(value: object) -> dict | None:
    """The wrapper's ``{page, error}``, or nothing. The file is ours, but it
    arrives over the network like any other document: a half-written or
    hand-edited one must not reach the page as a field of the wrong shape."""
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if not isinstance(error, str):
        return None
    return {"page": _str_or_none(value.get("page")), "error": error[:MAX_FIELD]}


def _age_seconds(updated_at: str | None, now: float) -> int | None:
    """Seconds since ``updated_at``, on the API's own clock (``now``, from
    ``time.time()`` at fetch time) — never the browser's. A page open on a
    laptop with a wrong clock must not read "0 s ago" (or a negative age)
    just because its own idea of "now" disagrees with the server's; the
    frontend renders this number as-is, it never computes its own."""
    if updated_at is None:
        return None
    try:
        then = datetime.fromisoformat(updated_at)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        then = then.timestamp()
    except ValueError:
        return None
    return max(0, round(now - then))


#: A volume's quality block names at most this many of its worst pages; the
#: wrapper writes five (quality.LOWEST), anything longer is not ours.
MAX_LOWEST = 5

#: No volume has this many pages; anything larger is not ours.
MAX_SCORED = 10_000_000


def _score(value: object) -> float | None:
    ok = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )
    return float(value) if ok else None


def _quality(value: object, pages: int = MAX_SCORED) -> dict | None:
    """The wrapper's quality block (docs: reference/s3-layout), or nothing.
    A mean outside [0, 1], a negative count or a string is not one of ours:
    dropped whole rather than drawn. ``pages`` is the volume's page total:
    more pages scored than the volume has is not ours either, and would
    outweigh every other volume in the campaign's mean."""
    if not isinstance(value, dict):
        return None
    mean, low = _score(value.get("mean")), _score(value.get("min"))
    scored = value.get("scored")
    if (
        mean is None
        or low is None
        or not isinstance(scored, int)
        or isinstance(scored, bool)
        or scored < 1
        or scored > min(pages, MAX_SCORED)
    ):
        return None
    lowest = []
    seen: set[str] = set()
    raw_lowest = value.get("lowest")
    for entry in raw_lowest if isinstance(raw_lowest, list) else []:
        if len(lowest) == MAX_LOWEST:
            break
        if not isinstance(entry, dict):
            continue
        q = _score(entry.get("quality"))
        page = _str_or_none(entry.get("page"))
        canvas = entry.get("canvas")
        # A page named twice is kept once, the first time (the wrapper ranks
        # lowest first): the frontend keys its list by page.
        if q is None or page is None or page in seen:
            continue
        seen.add(page)
        ok_canvas = (
            isinstance(canvas, int) and not isinstance(canvas, bool) and canvas >= 0
        )
        lowest.append(
            {"page": page, "quality": q, "canvas": canvas if ok_canvas else None}
        )
    return {
        "mean": mean,
        "min": low,
        "scored": scored,
        "model": _str_or_none(value.get("model")),
        "revision": _str_or_none(value.get("revision")),
        "lowest": lowest,
    }


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
        "quality": _quality(doc.get("quality"), doc["pages_total"]),
    }


def _from_manifest(doc: dict, now: float) -> dict | None:
    """The same counts off ``manifest.json``, for a volume finished before
    the wrapper wrote progress files at all. A skipped page is one an earlier
    run produced: it is in the bucket, so it counts as done; a failed one is
    a page the volume completed without (the product owner, 2026-09-14), and
    a done row has to say how many of those there were rather than read as a
    clean volume."""
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
        # The per-page reason IS in manifest.json's `results`, but naming it
        # here would mean picking a "most recent" failure out of a document
        # that records no order; progress.json, which every current wrapper
        # writes and which this is only the fallback for, carries the one the
        # run itself saw last.
        "lastError": None,
        "errors": 0,
        # manifest.json existing at all means the viewer manifest does too
        # (publish.py writes iiif.json before it, when any page's dims
        # resolved) -- true is the honest answer for a finished volume.
        "viewerPublished": True,
        "quality": _quality(doc.get("quality"), doc["pages"]),
    }


def _aged(value: dict | None, now: float) -> dict | None:
    """A cached row with its ``ageSeconds`` recomputed for this request. The
    counts in a cached row are stale by at most the TTL, but the age is
    stale by however long the row has been cached -- an hour, for a done
    volume -- and "updated 8 s ago" then said so all afternoon (2026-09-14
    audit). The timestamp it is computed from is in the row already, so this
    costs nothing and re-fetches nothing."""
    if value is None or value.get("updatedAt") is None:
        return value
    return {**value, "ageSeconds": _age_seconds(value["updatedAt"], now)}


def _encoded(response: httpx.Response) -> bool:
    coding = response.headers.get("content-encoding", "").strip().lower()
    return coding not in ("", "identity")


class _NoAnswer(Exception):
    """The bucket did not answer: unreachable, timed out, busy, or a 5xx."""


class ProgressReader:
    """One HTTP client and one small cache for the life of the app."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=TIMEOUT)
        #: url -> (expiry, progress, whether the bucket answered at all)
        self._cache: dict[str, tuple[float, dict | None, bool]] = {}
        #: Held around every touch of the cache, never across a GET. The
        #: reader is shared by every thread of the pool, and once the cache
        #: is full -- the normal state at scale -- two requests evicting
        #: the same oldest key raised a KeyError (2026-09-23 audit).
        self._lock = threading.Lock()

    def fetch(self, results_base: str, volume_id: str, state: str) -> dict | None:
        """This volume's progress, or ``None``. ``results_base`` is the row's
        own (``<public_results_base>/<namespace>/<pipeline>``)."""
        return self._read(results_base, volume_id, state, network=True)[1]

    def cached(
        self, results_base: str, volume_id: str, state: str
    ) -> tuple[bool, dict | None]:
        """``(known, progress)`` from the cache alone, never a GET. Known is
        an answer the bucket gave, an absent file included."""
        return self._read(results_base, volume_id, state, network=False)

    def _read(
        self, results_base: str, volume_id: str, state: str, network: bool
    ) -> tuple[bool, dict | None]:
        if state == "pending":
            return True, None  # no pod has run: there is nothing in the bucket
        # One clock read per call, not per cache miss -- and a cached hit
        # has its ageSeconds recomputed against it (`_aged`), so the counts
        # in a row can be as stale as the TTL but "updated N ago" never is.
        now = time.time()
        # Encoded: volume ids come off a campaign's volumes.txt, a file
        # people edit in a git repo, and an id with `../` in it was
        # normalised by the client into a request for another key
        # (2026-09-14 audit).
        base = f"{results_base}/{quote(volume_id, safe='')}"
        known, found = self._cached(
            f"{base}/progress.json", _from_progress, state, now, network
        )
        if known and found is None and state in _FINISHED:
            # Written by a wrapper that predates progress.json. One GET more,
            # cached for the hour: a finished volume is finished.
            known, found = self._cached(
                f"{base}/manifest.json", _from_manifest, state, now, network
            )
        return known, found

    def _cached(
        self,
        url: str,
        parse: Callable[[dict, float], dict | None],
        state: str,
        now: float,
        network: bool,
    ) -> tuple[bool, dict | None]:
        monotonic_now = time.monotonic()
        with self._lock:
            hit = self._cache.get(url)
        if hit is not None and hit[0] > monotonic_now:
            return hit[2], _aged(hit[1], now)
        if not network:
            return False, None
        answered, value = self._get(url, parse, now)
        # Only an answer about a volume that is over is kept for the hour --
        # an absent file included, since its pod wrote what it ever will. A
        # bucket that did not answer is asked again soon.
        ttl = DONE_TTL if state in _OVER and answered else RUNNING_TTL
        with self._lock:
            self._cache.pop(url, None)
            if len(self._cache) >= MAX_ENTRIES:
                del self._cache[next(iter(self._cache))]  # the oldest answer
            self._cache[url] = (monotonic_now + ttl, value, answered)
        return answered, value

    def _get(
        self, url: str, parse: Callable[[dict, float], dict | None], now: float
    ) -> tuple[bool, dict | None]:
        """``(answered, progress)``: a 404 is an answer, a 5xx or a
        connection that failed is not."""
        try:
            doc = self._body(url)
        except _NoAnswer:
            return False, None
        except Exception:
            return True, None  # not JSON at all: the bucket did answer
        return True, parse(doc, now) if isinstance(doc, dict) else None

    def _body(self, url: str) -> object:
        """The document at ``url``, read in chunks and abandoned past
        ``MAX_BODY``. Redirects are not followed: the URL is built from an
        operator's results base, and a bucket answering it with a Location
        is not somewhere this pod should go next.

        Asked for, and read, exactly as stored. Anything that can write to
        the bucket can store the file with `Content-Encoding: gzip`, and the
        cap counted what the client had already inflated -- 64 KiB off the
        wire could be 64 MiB in this pod before it looked (2026-09-23
        audit). The wrapper never compresses it, so an encoded answer is not
        our file, and it is dropped unread like any other."""
        try:
            with self._client.stream(
                "GET",
                url,
                headers={"Accept-Encoding": "identity"},
                follow_redirects=False,
            ) as response:
                if response.status_code >= 500 or response.status_code in (408, 429):
                    raise _NoAnswer(response.status_code)
                if response.status_code != 200 or _encoded(response):
                    return None
                body = bytearray()
                # No encoding (checked above), so nothing here inflates.
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        return None
        except httpx.HTTPError as e:
            raise _NoAnswer from e
        return json.loads(body)
