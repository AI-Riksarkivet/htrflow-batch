"""One page, fetched safely (docs: wrapper).

``fetch_page`` is the unit of work the bounded-lookahead pool in
``stream.PageStream`` runs: one image, retried with backoff, never raising —
a page it cannot fetch comes back as a ``FetchResult`` carrying the error,
which the stream records and the verify gate acts on.

Every response is checked before it is kept (W4): the body must be non-empty
and start with a known raster signature (JPEG/PNG/TIFF/GIF/BMP/WebP/JP2), and
an obviously textual Content-Type (text/*, HTML, JSON, XML) is refused
outright — a 200 login page used to be saved as the JPEG and burn a whole
attempt inside htrflow. Bodies are streamed to disk under ``max_bytes``
(``FETCH_MAX_BYTES``); a partial file is unlinked on any write failure (W5).

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
from pathlib import Path

import httpx
from pydantic import BaseModel

from .iiif import PageRef

#: Default cap on one image body (env ``FETCH_MAX_BYTES``; docs: wrapper).
FETCH_MAX_BYTES = 64 * 1024 * 1024

_CHUNK = 256 * 1024

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


class _Reject(Exception):
    """A response that must not be kept; ``retry`` says whether trying the
    same URL again can help."""

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
            for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                if not checked:
                    checked = True
                    if not looks_like_image(chunk):
                        raise _Reject(f"not an image: body starts with {chunk[:16]!r}")
                size += len(chunk)
                if size > max_bytes:
                    raise _Reject(f"too large: > {max_bytes} bytes", retry=False)
                f.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise _Reject("empty body")
    return size


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
    retries: int,
    backoff: float,
    max_bytes: int = FETCH_MAX_BYTES,
    stop: threading.Event | None = None,
) -> FetchResult:
    last = "unknown error"
    url = page.image_url
    path = dest_dir / f"{page.name}.jpg"
    for attempt in range(retries):
        if stop is not None and stop.is_set():
            return FetchResult(page=page, path=None, error="stopped: run aborted")
        try:
            with client.stream("GET", url, timeout=120, follow_redirects=True) as resp:
                if resp.status_code == 200:
                    size = _save(resp, path, max_bytes)
                    return FetchResult(page=page, path=path, error=None, size=size)
                last = f"HTTP {resp.status_code}"
                if resp.status_code == 400:
                    # Level1 servers 400 sized requests wider than the original
                    # (no upscaling); retry unscaled instead of failing the page.
                    fallback = re.sub(r"/full/\d+,/", "/full/max/", url)
                    if fallback != url:
                        url = fallback
                        continue
        except _Reject as e:
            last = str(e)
            if not e.retry:
                break
        except Exception as e:
            last = describe(e)
        # Skip sleep after final attempt
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    return FetchResult(page=page, path=None, error=last)
