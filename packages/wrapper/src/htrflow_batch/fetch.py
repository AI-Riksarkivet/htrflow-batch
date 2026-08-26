"""Bounded-lookahead downloader (docs: wrapper).

Runs in a plain thread with a separate relay thread. Main thread acquires
bounded slots and submits downloads to a pool of worker threads via httpx sync
client. Relay thread enqueues results in submission order while main thread may
block acquiring slots, preventing deadlock. `slots` (Semaphore(lookahead_pages))
bounds pages-in-flight-or-unconsumed so tmpfs never holds more than the window.

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

import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


def _fetch_one(
    page: PageRef,
    dest_dir: Path,
    client: httpx.Client,
    retries: int,
    backoff: float,
    max_bytes: int = FETCH_MAX_BYTES,
) -> FetchResult:
    last = "unknown error"
    url = page.image_url
    path = dest_dir / f"{page.name}.jpg"
    for attempt in range(retries):
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
            last = repr(e)
        # Skip sleep after final attempt
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    return FetchResult(page=page, path=None, error=last)


def run_downloader(
    pages: list[PageRef],
    dest_dir: Path,
    out_queue: queue.Queue,
    slots: threading.Semaphore,
    client: httpx.Client,
    concurrency: int = 12,
    retries: int = 3,
    backoff: float = 0.5,
    max_bytes: int = FETCH_MAX_BYTES,
) -> int:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Use a separate thread to enqueue results in order.
    # This allows results to be available on the queue even when the main
    # thread is blocked acquiring slots, preventing deadlock.
    futures_queue: queue.Queue = queue.Queue()
    total_holder = [0]  # Use list to allow mutation in nested function
    done_event = threading.Event()

    def enqueue_results_in_order():
        while True:
            try:
                page_and_fut = futures_queue.get(timeout=0.1)
            except queue.Empty:
                if done_event.is_set():
                    break
                continue
            page, fut = page_and_fut
            try:
                result = fut.result()  # Block until this future completes
            except Exception as e:
                # Belt-and-braces: if fut.result() raises unexpectedly (should not
                # happen since _fetch_one catches all exceptions), create error result
                result = FetchResult(page=page, path=None, error=repr(e))
            total_holder[0] += result.size
            out_queue.put(result)

    enqueue_thread = threading.Thread(target=enqueue_results_in_order, daemon=True)
    enqueue_thread.start()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for page in pages:
            slots.acquire()  # bounded lookahead: consumer releases
            fut = pool.submit(
                _fetch_one, page, dest_dir, client, retries, backoff, max_bytes
            )
            futures_queue.put((page, fut))

        done_event.set()

    # Wait for enqueue thread to finish
    enqueue_thread.join()
    out_queue.put(None)
    return total_holder[0]
