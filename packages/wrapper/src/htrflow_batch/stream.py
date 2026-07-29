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


def consume(
    out_queue: "queue.Queue",
    slots: threading.Semaphore,
    process: ProcessFn,
    upload: UploadFn,
    keep_images: bool = False,
) -> StreamStats:
    stats = StreamStats()
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
            else:
                t0 = time.monotonic()
                files = process(item.path)
                upload(name, files)
                stats.results[name] = PageOutcome(
                    status="ok", seconds=time.monotonic() - t0
                )
        except Exception as e:  # drain-what-you-can; verify gate decides later
            stats.results[name] = PageOutcome(status="failed", error=repr(e))
        finally:
            # Clean up image (outcome recorded, so deletion failure doesn't affect it)
            if item.path is not None and not keep_images:
                try:
                    item.path.unlink(missing_ok=True)
                except Exception:
                    pass  # Ignore deletion errors; outcome is already recorded
            slots.release()
