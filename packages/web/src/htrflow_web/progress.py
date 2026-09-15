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

import json
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
#: without limit; the whole thing goes rather than evicting cleverly, since a
#: refill is one cheap GET per row on screen.
MAX_ENTRIES = 5000

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
        # One clock read per call, not per cache miss -- and a cached hit
        # has its ageSeconds recomputed against it (`_aged`), so the counts
        # in a row can be as stale as the TTL but "updated N ago" never is.
        now = time.time()
        # Encoded: volume ids come off a campaign's volumes.txt, a file
        # people edit in a git repo, and an id with `../` in it was
        # normalised by the client into a request for another key
        # (2026-09-14 audit).
        base = f"{results_base}/{quote(volume_id, safe='')}"
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
            return _aged(hit[1], now)
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
            doc = self._body(url)
        except Exception:
            return None  # unreachable, timed out, or not JSON at all
        return parse(doc, now) if isinstance(doc, dict) else None

    def _body(self, url: str) -> object:
        """The document at ``url``, read in chunks and abandoned past
        ``MAX_BODY``. Redirects are not followed: the URL is built from an
        operator's results base, and a bucket answering it with a Location
        is not somewhere this pod should go next."""
        with self._client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                return None
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BODY:
                    return None
        return json.loads(body)
