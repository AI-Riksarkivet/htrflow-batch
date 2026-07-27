"""Consumer side of the streaming loop (DESIGN.md §5.1 stage 3)."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fetch import FetchResult


@dataclass
class PageOutcome:
    status: str                  # "ok" | "failed" | "skipped"
    seconds: float = 0.0
    error: str | None = None


@dataclass
class StreamStats:
    results: dict[str, PageOutcome] = field(default_factory=dict)
    stall_seconds: float = 0.0


ProcessFn = Callable[[Path], "dict[str, Path]"]
UploadFn = Callable[[str, "dict[str, Path]"], None]


def consume(out_queue: "queue.Queue", slots: threading.Semaphore,
            process: ProcessFn, upload: UploadFn,
            keep_images: bool = False) -> StreamStats:
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
                stats.results[name] = PageOutcome("failed", error=item.error)
                continue
            t0 = time.monotonic()
            files = process(item.path)
            upload(name, files)
            if not keep_images:
                item.path.unlink(missing_ok=True)
            stats.results[name] = PageOutcome("ok", seconds=time.monotonic() - t0)
        except Exception as e:  # drain-what-you-can; verify gate decides later
            stats.results[name] = PageOutcome("failed", error=repr(e))
        finally:
            slots.release()
