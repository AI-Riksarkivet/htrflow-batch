"""Bounded-lookahead downloader (DESIGN.md §5.1 downloader pool).

Runs in a plain thread; uses a bounded pool of worker threads via httpx sync
client for simplicity and determinism. `slots` (Semaphore(lookahead_pages))
bounds pages-in-flight-or-unconsumed so tmpfs never holds more than the window.
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from .iiif import PageRef


@dataclass
class FetchResult:
    page: PageRef
    path: Path | None
    error: str | None
    size: int = 0


def _fetch_one(page: PageRef, dest_dir: Path, client: httpx.Client,
               retries: int, backoff: float) -> FetchResult:
    last = "unknown error"
    for attempt in range(retries):
        try:
            resp = client.get(page.image_url, timeout=120, follow_redirects=True)
            if resp.status_code == 200:
                path = dest_dir / f"{page.name}.jpg"
                path.write_bytes(resp.content)
                return FetchResult(page, path, None, len(resp.content))
            last = f"HTTP {resp.status_code}"
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(backoff * (2 ** attempt))
    return FetchResult(page, None, last)


def run_downloader(pages: list[PageRef], dest_dir: Path, out_queue: queue.Queue,
                   slots: threading.Semaphore, client: httpx.Client,
                   concurrency: int = 12, retries: int = 3,
                   backoff: float = 0.5) -> int:
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
                fut = futures_queue.get(timeout=0.1)
            except queue.Empty:
                if done_event.is_set():
                    break
                continue
            result = fut.result()  # Block until this future completes
            total_holder[0] += result.size
            out_queue.put(result)

    enqueue_thread = threading.Thread(target=enqueue_results_in_order, daemon=True)
    enqueue_thread.start()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for page in pages:
            slots.acquire()          # bounded lookahead: consumer releases
            fut = pool.submit(_fetch_one, page, dest_dir, client, retries, backoff)
            futures_queue.put(fut)

        done_event.set()

    # Wait for enqueue thread to finish
    enqueue_thread.join()
    out_queue.put(None)
    return total_holder[0]
