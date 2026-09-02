"""The streaming loop (docs: wrapper): ``fetched()`` starts downloading the
moment it is called — so a model load overlaps the first pages — and
``consume()`` iterates it, processing and uploading each page as it lands."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Callable, Iterable, Iterator

import httpx
from pydantic import BaseModel, Field

from .fetch import FETCH_MAX_BYTES, FetchResult, fetch_page
from .iiif import PageRef

log = logging.getLogger("htrflow_batch")


class PageOutcome(BaseModel):
    status: str  # "ok" | "failed" | "skipped"
    seconds: float = 0.0
    error: str | None = None


class StreamStats(BaseModel):
    results: dict[str, PageOutcome] = Field(default_factory=dict)
    stall_seconds: float = 0.0


ProcessFn = Callable[[Path], "dict[str, Path]"]
UploadFn = Callable[[str, "dict[str, Path]"], None]

#: Consecutive store failures after which the run is abandoned (W6).
MAX_UPLOAD_FAILURES = 5


def _failed(stats: "StreamStats", name: str, error: str | None) -> None:
    """Record a page failure AND say why: a run the verify gate abandons never
    publishes manifest.json, so the run log is where the cause survives."""
    log.warning("page %s failed: %s", name, error)
    stats.results[name] = PageOutcome(status="failed", error=error)


class UploadOutage(RuntimeError):
    """The result store failed for N pages in a row: transient, abort now
    rather than drain the whole volume through a dead bucket."""


class PageStream:
    """Bounded-lookahead download stream, iterated once.

    ``lookahead`` bounds *submission*: a page's slot frees only when the
    consumer comes back for the next result — by then it has deleted the
    image — so tmpfs never holds more than ``lookahead`` pages, exactly what
    the Semaphore this replaces guaranteed. Results come in submission order,
    i.e. manifest order — waiting on the head of the queue, exactly as the
    relay thread this replaces did. ``stop`` (W10): once set nothing more is
    submitted and ``close()`` cancels what is queued, so a run that has
    already failed need not wait out its downloads.
    """

    def __init__(
        self,
        pages: list[PageRef],
        dest_dir: Path,
        client: httpx.Client,
        *,
        lookahead: int,
        concurrency: int = 12,
        retries: int = 3,
        backoff: float = 0.5,
        max_bytes: int = FETCH_MAX_BYTES,
        stop: threading.Event | None = None,
    ) -> None:
        self.bytes_fetched = 0
        self.error: str | None = None
        dest = Path(dest_dir)
        self._fetch = partial(
            fetch_page,
            dest_dir=dest,
            client=client,
            retries=retries,
            backoff=backoff,
            max_bytes=max_bytes,
            stop=stop,
        )
        self._queued = list(pages)
        self._lookahead = max(1, lookahead)
        self._stop = stop
        self._live: deque[tuple[Future, PageRef]] = deque()
        self._outstanding = 0  # submitted, not yet released by the consumer
        self._pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        # The first window goes out here, on the caller's thread, so the model
        # load that follows overlaps it. A failure must still leave a stream
        # that terminates (the downloader's ``None`` sentinel did that): it
        # yields nothing, and the verify gate reports the pages as missing.
        try:
            dest.mkdir(parents=True, exist_ok=True)
            self._fill()
        except Exception as e:
            self._abandon(e)

    def _fill(self) -> None:
        while self._queued and self._outstanding < self._lookahead:
            if self._stop is not None and self._stop.is_set():
                self._queued.clear()  # W10: an aborted run submits no more
                return
            page = self._queued.pop(0)
            self._live.append((self._pool.submit(self._fetch, page), page))
            self._outstanding += 1

    def _abandon(self, exc: Exception) -> None:
        log.error("downloader failed: %r", exc)
        self.error = repr(exc)
        self._queued.clear()

    def close(self) -> None:
        """Never waits: a SIGTERM or MAX_SECONDS exit must not block on a
        download sitting in its 120 s timeout (why ``_hard_exit`` exists)."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __iter__(self) -> Iterator[FetchResult]:
        try:
            while self._live:
                fut, page = self._live.popleft()
                try:
                    result = fut.result()  # in submission order, head first
                except Exception as e:  # fetch_page catches its own errors
                    result = FetchResult(page=page, path=None, error=repr(e))
                self.bytes_fetched += result.size
                yield result
                # Back from the consumer: page done, image deleted —
                # its slot frees and the next page goes out.
                self._outstanding -= 1
                self._fill()
        except Exception as e:
            self._abandon(e)
        finally:
            self.close()


#: Start the bounded-lookahead downloads:
#: ``fetched(pages, dest_dir, client, lookahead=…)`` hands back the running
#: stream to iterate (see ``PageStream``).
fetched = PageStream


def consume(
    items: Iterable[FetchResult],
    process: ProcessFn,
    upload: UploadFn,
    keep_images: bool = False,
    max_upload_failures: int = MAX_UPLOAD_FAILURES,
) -> StreamStats:
    stats = StreamStats()
    upload_failures = 0
    pages = iter(items)
    while True:
        t_wait = time.monotonic()
        item = next(pages, None)  # GPU stall = waiting for the next page
        stats.stall_seconds += time.monotonic() - t_wait
        if item is None:
            return stats
        name = item.page.name
        try:
            if item.path is None:
                _failed(stats, name, item.error)
                continue
            t0 = time.monotonic()
            try:
                files = process(item.path)
            except Exception as e:  # drain-what-you-can; verify gate decides later
                _failed(stats, name, repr(e))
                continue
            try:
                upload(name, files)
            except ValueError as e:
                # the page's own outputs are bad (missing format, malformed
                # XML — store.upload_page): a page failure, not an outage
                _failed(stats, name, repr(e))
                continue
            except Exception as e:
                _failed(stats, name, repr(e))
                upload_failures += 1
                if upload_failures >= max_upload_failures:
                    raise UploadOutage(
                        f"{upload_failures} consecutive upload failures, last: {e!r}"
                    ) from e
                continue
            upload_failures = 0
            stats.results[name] = PageOutcome(
                status="ok", seconds=time.monotonic() - t0
            )
        finally:
            # Clean up image (outcome recorded, so deletion failure doesn't affect it)
            if item.path is not None and not keep_images:
                try:
                    item.path.unlink(missing_ok=True)
                except Exception:
                    pass  # Ignore deletion errors; outcome is already recorded
