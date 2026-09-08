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
from .iiif import PageRef, redact_urls
from .logship import LogCapture
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

#: How much of a failed page's error goes in the progress file. It is a chip
#: on a campaign page, not the run log: enough to recognise the failure, not
#: a traceback.
LAST_ERROR_CHARS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Progress:
    """The run's own view of itself, as the bucket sees it.

    ``stats`` is the stream's ``StreamStats`` — the same object the manifest
    is built from, so the page counts here can never disagree with the ones
    published at the end.
    """

    def __init__(
        self, cfg: Config, store: ResultStore, capture: LogCapture | None = None
    ) -> None:
        self.cfg = cfg
        self.store = store
        #: Where the ERROR count comes from; absent in unit tests, which
        #: install no logging of their own.
        self.capture = capture
        self.stage = "setup"
        self.pages: list[PageRef] = []
        self.source: dict = {}
        self.stats: StreamStats | None = None
        self.last_page: str | None = None
        self.started_at = _now()
        self._published = 0
        #: True once an iiif.json PUT has actually succeeded -- interim
        #: (below) or final (main.py, after publish.run). The frontend keys
        #: "open in the viewer" off this, never off a page count: at
        #: PUBLISH_EVERY_PAGES=10, a volume of 9 pages or fewer never crosses
        #: the old count-based threshold before it finishes, and pages 1-9 of
        #: a bigger one would have linked to a manifest that is not there yet.
        self.viewer_published = False

    def _counts(self) -> tuple[int, int]:
        """Done and failed. A skipped page is one an earlier run finished and
        this one resumed past: it IS in the bucket, so it counts as done —
        else a volume resumed at page 600 of 638 would report zero."""
        results = list(self.stats.results.values()) if self.stats else []
        done = sum(1 for r in results if r.status in ("ok", "skipped"))
        return done, sum(1 for r in results if r.status == "failed")

    def _last_error(self) -> dict | None:
        """The most recent failed page and the sentence the wrapper recorded
        for it. Without it the campaign page can say "1 page failed" and
        nothing else, and the reason is a log download away. Redacted like
        every other error that reaches the public bucket (S6), and bounded:
        this is a chip, not a traceback."""
        results = list(self.stats.results.items()) if self.stats else []
        for name, result in reversed(results):
            if result.status == "failed":
                error = redact_urls(result.error or "")
                if len(error) > LAST_ERROR_CHARS:
                    error = error[:LAST_ERROR_CHARS] + "..."
                return {"page": name, "error": error}
        return None

    def body(self) -> dict:
        done, failed = self._counts()
        return {
            "stage": self.stage,
            "pages_total": len(self.pages),
            "pages_done": done,
            "pages_failed": failed,
            "last_page": self.last_page,
            # Why the page count is not the whole story (the product owner,
            # 2026-09-08): a run with an exception in its log should say so
            # on the front page.
            "last_error": self._last_error(),
            "errors": self.capture.errors if self.capture is not None else 0,
            "viewer_published": self.viewer_published,
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
        done, _ = self._counts()
        # Before the write: _publish_viewer can flip viewer_published, and
        # that belongs in THIS page's progress.json, not the next one's.
        if done - self._published >= PUBLISH_EVERY_PAGES:
            self._published = done
            self._publish_viewer(done)
        self.write()

    def _publish_viewer(self, done: int) -> None:
        """``iiif.json`` for the pages this run has finished. Only the dims
        already in memory (``store.page_dims``, off the ALTO the upload
        parsed anyway) — reading a resumed run's earlier ALTOs back would be
        one S3 GET per page in the middle of the page loop. The final publish
        does read them, so the published manifest always ends up complete.

        ``done`` counts resumed pages too (``_counts``), but ``store.page_dims``
        only ever holds THIS run's own pages -- a run resumed at page 600 of
        638 has no dims for the 600 it skipped. Publishing here with only the
        10 or so this run has actually processed would overwrite a complete
        638-canvas iiif.json with a 10-canvas one, so this is skipped whenever
        the dims in hand cover fewer pages than ``done`` says are finished;
        the final publish (which DOES read a resumed page's ALTO back) still
        writes the complete manifest, so a resumed run is never worse off
        than today's "no interim iiif.json at all" (docs: s3-layout)."""
        dims = known_dims(self.store, self.pages)
        if not dims or len(dims) < done:
            return
        try:
            self.store.put_json(
                "iiif.json",
                build_viewer_manifest(self.cfg, self.source, self.pages, dims),
            )
            self.viewer_published = True
        except Exception as e:
            log.warning("could not publish the interim viewer manifest: %r", e)
