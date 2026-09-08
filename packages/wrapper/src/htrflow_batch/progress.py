"""How far this volume has got, written where a reader can see it.

Two objects next to the results (docs: reference/s3-layout), both owned by
this module: ``progress.json``, rewritten after every page outcome and at
every stage change, and the *incremental* ``iiif.json``, rewritten every
``PUBLISH_EVERY_PAGES`` pages so a volume opens in the viewer while it is
still being processed instead of only after the last page.

Both writes are best-effort and neither may fail the run: losing a page's
work to a hiccup while writing a status file would be a worse bargain than
having no status file at all. ``manifest.json`` is untouched by all of this —
it is still written last, by publish, and is still the completion marker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Config
from .iiif import PageRef
from .publish import known_dims
from .store import ResultStore
from .stream import StreamStats
from .viewer import build_viewer_manifest

log = logging.getLogger("htrflow_batch")

#: Pages between two rewrites of the incremental viewer manifest. Counted in
#: pages rather than on the run log's LOG_SHIP_SECONDS clock because the
#: manifest changes only when a page finishes: a timer would rewrite the
#: identical document on a slow page, and would be a second cadence to reason
#: about, while the page loop is a clock this code is already standing in.
PUBLISH_EVERY_PAGES = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Progress:
    """The run's own view of itself, as the bucket sees it.

    ``stats`` is the stream's ``StreamStats`` — the same object the manifest
    is built from, so the page counts here can never disagree with the ones
    published at the end.
    """

    def __init__(self, cfg: Config, store: ResultStore) -> None:
        self.cfg = cfg
        self.store = store
        self.stage = "setup"
        self.pages: list[PageRef] = []
        self.source: dict = {}
        self.stats: StreamStats | None = None
        self.last_page: str | None = None
        self.started_at = _now()
        self._published = 0

    def _counts(self) -> tuple[int, int]:
        """Done and failed. A skipped page is one an earlier run finished and
        this one resumed past: it IS in the bucket, so it counts as done —
        else a volume resumed at page 600 of 638 would report zero."""
        results = list(self.stats.results.values()) if self.stats else []
        done = sum(1 for r in results if r.status in ("ok", "skipped"))
        return done, sum(1 for r in results if r.status == "failed")

    def body(self) -> dict:
        done, failed = self._counts()
        return {
            "stage": self.stage,
            "pages_total": len(self.pages),
            "pages_done": done,
            "pages_failed": failed,
            "last_page": self.last_page,
            "started_at": self.started_at,
            "updated_at": _now(),
        }

    def write(self) -> None:
        try:
            self.store.put_progress(self.body())
        except Exception as e:
            log.debug("could not write progress.json: %r", e)

    def stage_changed(self, stage: str) -> None:
        """Wired to ``RunState.stage``, so every stage the run passes through
        reaches the bucket without a call at each assignment."""
        self.stage = stage
        self.write()

    def after_page(self, name: str) -> None:
        self.last_page = name
        self.write()
        done, _ = self._counts()
        if done - self._published >= PUBLISH_EVERY_PAGES:
            self._published = done
            self._publish_viewer()

    def _publish_viewer(self) -> None:
        """``iiif.json`` for the pages this run has finished. Only the dims
        already in memory (``store.page_dims``, off the ALTO the upload
        parsed anyway) — reading a resumed run's earlier ALTOs back would be
        one S3 GET per page in the middle of the page loop. The final publish
        does read them, so the published manifest always ends up complete."""
        dims = known_dims(self.store, self.pages)
        if not dims:
            return
        try:
            self.store.put_json(
                "iiif.json",
                build_viewer_manifest(self.cfg, self.source, self.pages, dims),
            )
        except Exception as e:
            log.warning("could not publish the interim viewer manifest: %r", e)
