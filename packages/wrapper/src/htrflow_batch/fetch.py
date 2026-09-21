"""One page, fetched safely (docs: wrapper).

``fetch_page`` is the unit of work the bounded-lookahead pool in
``stream.PageStream`` runs: one image, retried with backoff, never raising —
a page it cannot fetch comes back as a ``FetchResult`` carrying the error,
which the stream records and the verify gate acts on.

A failure is classified (3095). One that says "not now" -- a network error,
the download deadline, 408/425/429, a 5xx other than 501/505, an HTML or
empty answer where an image belongs (a WAF challenge, an overloaded CDN) --
is asked again, honouring ``Retry-After``, and if it outlasts the attempts
the page comes back ``transient``: the stream leaves it out of ``failed``, so
verify finds it missing, the index is retried and resume redoes just it. One
that says "not this" -- any other status, a body over a cap, an encoding not
asked for -- fails the page at once, and a retry would not change it.

Every response is checked before it is kept (W4): the body must be non-empty
and start with a known raster signature (JPEG/PNG/TIFF/GIF/BMP/WebP/JP2), and
an obviously textual Content-Type (text/*, HTML, JSON, XML) is refused
outright — a 200 login page used to be saved as the JPEG and burn a whole
attempt inside htrflow. Bodies are streamed to disk under ``max_bytes``
(``FETCH_MAX_BYTES``), counted on the decoded bytes (``bounded``, 3062); a
partial file is unlinked on any write failure (W5).

Known limit — service-less canvases: ``MAX_IMAGE_WIDTH`` is applied through
the IIIF Image API (``/full/<w>,/``). A canvas that carries no image service
(synthetic ``images:`` manifests, static painting bodies) is fetched at its
native size: no server-side downscale is possible, ``FETCH_MAX_BYTES`` is the
only bound, and htrflow processes the full-resolution image (memory and
time scale with it). Keep such image lists pre-sized.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from .bounded import (
    ACCEPT_ENCODING,
    DOWNLOAD_DEADLINE_SECONDS,
    BadEncoding,
    Deadline,
    TooLarge,
    body_chunks,
)
from .iiif import PageRef

#: Default cap on one image body (env ``FETCH_MAX_BYTES``; docs: wrapper).
FETCH_MAX_BYTES = 64 * 1024 * 1024

#: Default cap on one image's DECODED size (env ``MAX_IMAGE_PIXELS``; docs:
#: wrapper). 100 MP is ~4x an A0 sheet at 400 dpi -- far above anything a
#: digitised page is, and far below what would exhaust a pod.
MAX_IMAGE_PIXELS = 100_000_000

#: Serialises the swap of Pillow's own limit in ``_check_pixels`` -- it is a
#: module global and the download pool has a dozen threads.
_PIXEL_GUARD = threading.Lock()

#: Content types that can never be a raster image; refused before reading.
_TEXTUAL_TYPES = ("text/", "application/json", "application/xml", "application/xhtml")

_IMAGE_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG",  # PNG
    b"GIF8",  # GIF
    b"II*\x00",  # TIFF LE
    b"MM\x00*",  # TIFF BE
    b"BM",  # BMP
    b"\x00\x00\x00\x0cjP  ",  # JP2 signature box
    b"\xff\x4f\xff\x51",  # J2K codestream
)


def looks_like_image(head: bytes) -> bool:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return any(head.startswith(m) for m in _IMAGE_MAGIC)


class FetchResult(BaseModel):
    page: PageRef
    path: Path | None
    error: str | None
    size: int = 0
    #: The page could not be fetched TODAY; the next attempt may (3095).
    transient: bool = False


#: Attempts per page, and the first wait between them (doubling after it).
#: With the download deadline this bounds a page: at most FETCH_ATTEMPTS x
#: the deadline, plus the waits -- inside the pod's own time budget by hours.
FETCH_ATTEMPTS = 4
FETCH_BACKOFF = 2.0

#: The longest ``Retry-After`` waited out between two attempts. A host asking
#: for longer is outlasting the attempts, and the index's retry, minutes
#: later, is the better place to ask again.
MAX_RETRY_AFTER = 60.0


def _transient_status(status: int) -> bool:
    """A status that says "not now": asked again, never recorded as the
    page's outcome. 501 and 505 are about what we asked, not when."""
    return status in (408, 425, 429) or (status >= 500 and status not in (501, 505))


def _transient_error(e: Exception) -> bool:
    """A transport failure a later attempt can get past -- not a URL httpx
    will never fetch, nor a request it will never send. An OSError is the
    workdir's, not the page's."""
    if isinstance(e, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)):
        return False
    return isinstance(e, (httpx.TransportError, OSError))


def _retry_after(resp: httpx.Response) -> float:
    """The server's ``Retry-After``, as seconds (0 when absent or unreadable),
    capped at ``MAX_RETRY_AFTER``. Delta-seconds or an HTTP-date."""
    value = resp.headers.get("Retry-After", "").strip()
    if value.isdigit():
        seconds = float(value)
    else:
        try:
            when = parsedate_to_datetime(value)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):  # junk, or a date without a zone
            return 0.0
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _pause(seconds: float, stop: threading.Event | None) -> None:
    """Wait between attempts, but not past the run's abort (W10)."""
    if stop is not None:
        stop.wait(seconds)
    else:
        time.sleep(seconds)


class _Reject(Exception):
    """A response that must not be kept; ``retry`` says whether trying the
    same URL again can help -- and so, once the attempts are spent, whether
    the page is transient rather than failed (3095)."""

    def __init__(self, msg: str, retry: bool = True):
        super().__init__(msg)
        self.retry = retry


def _save(resp: httpx.Response, path: Path, max_bytes: int) -> int:
    """Stream ``resp`` to ``path`` with the W4 checks; return bytes written.
    Never leaves a partial file behind."""
    ctype = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if ctype and ctype.startswith(_TEXTUAL_TYPES):
        raise _Reject(f"not an image: Content-Type {ctype}")
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise _Reject(f"too large: {declared} bytes > {max_bytes}", retry=False)
    size = 0
    checked = False
    try:
        with path.open("wb") as f:
            for chunk in body_chunks(resp, max_bytes):
                if not checked:
                    checked = True
                    if not looks_like_image(chunk):
                        raise _Reject(f"not an image: body starts with {chunk[:16]!r}")
                size += len(chunk)
                f.write(chunk)
    except (TooLarge, BadEncoding) as e:  # 3062: the same body tomorrow
        path.unlink(missing_ok=True)
        why = f"too large: {e}" if isinstance(e, TooLarge) else str(e)
        raise _Reject(why, retry=False) from e
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise _Reject("empty body")
    return size


def _check_pixels(path: Path, max_pixels: int) -> None:
    """W14 (2026-09-14, audit): bound what DECODING the page will cost.

    ``max_bytes`` bounds the download, and the two are not the same number: a
    few MB of JPEG can carry a gigapixel image, and htrflow decodes every page
    into memory -- so a pod was OOM-killed by a file that had passed every
    check the fetcher made. ``Image.open`` reads the header only, so this
    costs nothing per page. A file Pillow cannot read is NOT rejected here:
    that is htrflow's business, and is where a page that will not decode has
    always failed. The rejected file goes, like every other partial one.

    Pillow's own bomb guard is turned off for the header read, so
    ``max_pixels`` is the only gate and the wrapper is the one that says what
    happened. Both halves of that guard were in the way: above roughly twice
    ``Image.MAX_IMAGE_PIXELS`` (~179 MP) ``Image.open`` raises
    ``DecompressionBombError``, which the ``except`` below swallowed -- so a
    40000x40000 image passed the very check this function exists for -- and
    above ``Image.MAX_IMAGE_PIXELS`` itself (~89 MP, BELOW our own default)
    it warns, on pages nothing is wrong with. The lock keeps the swap from
    two pool threads overlapping; a header read is a few KB, so serialising
    them costs nothing."""
    from PIL import Image

    with _PIXEL_GUARD:
        limit, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, None
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            return  # not a size we can read: a decodability gate this is not
        finally:
            Image.MAX_IMAGE_PIXELS = limit
    if max_pixels and width * height > max_pixels:
        path.unlink(missing_ok=True)
        raise _Reject(
            f"too large: {width}x{height} = {width * height} pixels > {max_pixels}",
            retry=False,
        )


def describe(e: BaseException) -> str:
    """The sentence a failed page records -- and, through progress.json,
    the one the status page's notice shows. This package's own exceptions
    are sentences already (PipelineDead, the store's ValueError -- our class,
    or raised from our code), so they are shown as they are; one from
    elsewhere (htrflow, torch) keeps its type in front, since "'NoneType'
    object is not iterable" alone does not say what threw it. Never repr():
    PipelineDead("page 0044: ...") reached the notice chip verbatim on the
    first live run (2026-09-08)."""
    tb = e.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    raised_in = tb.tb_frame.f_globals.get("__name__", "") if tb else ""
    ours = raised_in.startswith("htrflow_batch") or type(e).__module__.startswith(
        "htrflow_batch"
    )
    text = str(e)
    if ours and text:
        return text
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


def fetch_page(
    page: PageRef,
    dest_dir: Path,
    client: httpx.Client,
    retries: int = FETCH_ATTEMPTS,
    backoff: float = FETCH_BACKOFF,
    max_bytes: int = FETCH_MAX_BYTES,
    max_pixels: int = MAX_IMAGE_PIXELS,
    stop: threading.Event | None = None,
    deadline: float = DOWNLOAD_DEADLINE_SECONDS,
) -> FetchResult:
    last, transient = "unknown error", True
    url = page.image_url
    path = dest_dir / f"{page.name}.jpg"
    attempt = 0
    while attempt < retries:
        if stop is not None and stop.is_set():
            return FetchResult(
                page=page, path=None, error="stopped: run aborted", transient=True
            )
        wait = 0.0
        clock = Deadline(deadline)  # 3063: per attempt, whatever the reads do
        try:
            with (
                clock,
                client.stream(
                    "GET",
                    url,
                    headers={"Accept-Encoding": ACCEPT_ENCODING},
                    timeout=120,
                    follow_redirects=True,
                    extensions=clock.extensions,
                ) as resp,
            ):
                if resp.status_code == 200:
                    size = _save(resp, path, max_bytes)
                    if clock.expired:  # cut short, but ended like a whole body
                        path.unlink(missing_ok=True)
                        raise _Reject(clock.reason)
                    _check_pixels(path, max_pixels)  # W14
                    return FetchResult(page=page, path=path, error=None, size=size)
                last = f"HTTP {resp.status_code}"
                if resp.status_code == 400:
                    # Level1 servers 400 sized requests wider than the original
                    # (no upscaling); retry unscaled instead of failing the page.
                    fallback = re.sub(r"/full/\d+,/", "/full/max/", url)
                    if fallback != url:
                        # W13: a different URL, not another go at the one that
                        # failed, so it does not spend one of the page's
                        # attempts (and does not wait out a backoff to ask a
                        # question this code has already answered). It can
                        # happen once: the substitution is then a no-op and the
                        # next 400 is final like any other status below.
                        url = fallback
                        continue
                transient = _transient_status(resp.status_code)
                if not transient:
                    break
                wait = _retry_after(resp)
        except _Reject as e:
            last, transient = str(e), e.retry
            if not transient:
                break
        except Exception as e:
            last = clock.reason if clock.expired else describe(e)
            transient = clock.expired or _transient_error(e)
            if not transient:
                break
        attempt += 1
        if attempt < retries:  # no wait after the last attempt
            _pause(max(backoff * (2 ** (attempt - 1)), wait), stop)
    return FetchResult(page=page, path=None, error=last, transient=transient)
