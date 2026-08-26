"""Consumer side of the streaming loop (docs: wrapper)."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from .fetch import FetchResult


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


class UploadOutage(RuntimeError):
    """The result store failed for N pages in a row: transient, abort now
    rather than drain the whole volume through a dead bucket."""


def consume(
    out_queue: "queue.Queue",
    slots: threading.Semaphore,
    process: ProcessFn,
    upload: UploadFn,
    keep_images: bool = False,
    max_upload_failures: int = MAX_UPLOAD_FAILURES,
) -> StreamStats:
    stats = StreamStats()
    upload_failures = 0
    while True:
        t_wait = time.monotonic()
        item = out_queue.get()
        stats.stall_seconds += time.monotonic() - t_wait
        if item is None:
            return stats
        assert isinstance(item, FetchResult)
        name = item.page.name
        try:
            if item.path is None:
                stats.results[name] = PageOutcome(status="failed", error=item.error)
                continue
            t0 = time.monotonic()
            try:
                files = process(item.path)
            except Exception as e:  # drain-what-you-can; verify gate decides later
                stats.results[name] = PageOutcome(status="failed", error=repr(e))
                continue
            try:
                upload(name, files)
            except ValueError as e:
                # the page's own outputs are bad (missing format, malformed
                # XML — store.upload_page): a page failure, not an outage
                stats.results[name] = PageOutcome(status="failed", error=repr(e))
                continue
            except Exception as e:
                stats.results[name] = PageOutcome(status="failed", error=repr(e))
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
            slots.release()
