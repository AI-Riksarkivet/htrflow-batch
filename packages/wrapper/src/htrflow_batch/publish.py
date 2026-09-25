"""The publish stage (docs: wrapper): the viewer manifest, the pipeline copy,
and manifest.json LAST — its presence is the sole completion marker."""

from __future__ import annotations

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from . import quality as qp
from .config import Config
from .iiif import PageRef, redact_url, redact_urls, source_digest
from .store import ResultStore
from .stream import StreamStats
from .viewer import build_viewer_manifest, parse_alto_dims

log = logging.getLogger("htrflow_batch")


def known_dims(store: ResultStore, pages: list[PageRef]) -> dict:
    """Dimensions this run already holds, off the ALTO ``store.upload_page``
    parsed on its way past — no round-trip. Both the interim viewer manifest
    (progress.py) and the final one start here."""
    return {p.name: store.page_dims[p.name] for p in pages if p.name in store.page_dims}


def alto_dims(
    cfg: Config, store: ResultStore, pages: list[PageRef], uploaded: set[str]
) -> dict:
    """Page dimensions as actually processed: from the ALTO this run uploaded
    (store.upload_page parsed it there, so this costs no round-trip), or read
    back from S3 for a page a previous run published, so a resumed volume's
    viewer manifest stays complete -- and, on that same read-back, a resumed
    page's score (quality.py). A page whose ALTO will not parse is left out
    rather than failing the publish. A store error is NOT (audit 0923 W-6):
    skipped, it dropped that canvas from the final iiif.json for good, since
    manifest.json follows and nothing writes it again -- raised, the run
    exits 1 and the retry redoes only the publish."""
    dims = known_dims(store, pages)
    for p in pages:
        if p.name in dims or p.name not in uploaded:
            continue
        data = store.get_bytes(f"alto/{p.name}.xml")
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue  # an ALTO that will not parse is left out, not fatal
        # A resumed page's score comes back with its size (quality.py).
        if (score := qp.parse_alto_quality(root)) is not None:
            store.page_quality[p.name] = score
        try:
            dims[p.name] = parse_alto_dims(root)
        except ValueError:
            pass
    if len(dims) < len(pages):
        log.warning("viewer manifest covers %d/%d pages", len(dims), len(pages))
    if not dims:
        log.warning(
            "[%s] no ALTO dims resolved for any page; "
            "iiif.json not published, viewer_url will 404",
            cfg.volume_ref,
        )
    return dims


def _results_json(stats: StreamStats, quality: Mapping[str, float]) -> dict:
    """Per-page outcomes for manifest.json; error strings lose URL secrets (S6).
    A page's predicted quality (quality.py) rides along when it has one."""
    return {
        n: {
            "status": r.status,
            "seconds": round(r.seconds, 2),
            **({"error": redact_urls(r.error)} if r.error else {}),
            **({"quality": round(quality[n], qp.DIGITS)} if n in quality else {}),
        }
        for n, r in sorted(stats.results.items())
    }


def _canvas_id(canvas: dict) -> str | None:
    cid = canvas.get("id") or canvas.get("@id")
    return cid if isinstance(cid, str) else None


def _htrflow_version() -> str:
    try:
        from .driver import htrflow_version

        return htrflow_version()
    except Exception:
        return "unknown"


class _Unset:
    """Sentinel type for ``run_manifest``'s ``summary`` parameter: tells a
    caller-supplied ``None`` (there is no quality block) apart from no
    argument at all (compute it here). A plain ``None`` default cannot carry
    that distinction, since ``None`` is also the correct value for "no
    scores"."""


_UNSET = _Unset()


def run_manifest(
    cfg: Config,
    pages: list[PageRef],
    stats: StreamStats,
    source_manifest_url: str,
    pipeline_text: str,
    wall: float,
    bytes_fetched: int,
    quality: Mapping[str, float] | None = None,
    canvases: Sequence[str] = (),
    summary: dict | None | _Unset = _UNSET,
    image_cache: dict | None = None,
) -> dict:
    """The manifest.json body: what the volume is, what produced it, what came
    out, and what a resume or the Phase 2 gate reads back (docs: s3-layout).
    ``quality`` is a page's predicted score off its ALTO (quality.py); with
    none at all, the top-level ``quality`` key is absent, same as before this
    step existed. ``summary`` lets a caller that already computed the block
    (``run``, ahead of ``iiif.json``) pass it in -- including an explicit
    ``None``, meaning "no quality block" -- rather than have it built twice;
    a caller that omits the argument (the contract script) gets it computed
    here. ``image_cache`` is the run's ``ImageCache.report()`` (imagecache.py);
    with the cache off it is None and the ``image_cache`` key is absent."""
    ok_pages = [n for n, r in stats.results.items() if r.status == "ok"]
    failed_pages = [n for n, r in stats.results.items() if r.status == "failed"]
    scores = quality or {}
    body = {
        "volume": cfg.volume_ref,
        "pipeline_id": cfg.pipeline_id,
        "pipeline_sha256": hashlib.sha256(pipeline_text.encode()).hexdigest(),
        "pipeline_yaml": pipeline_text,
        "htrflow_version": _htrflow_version(),
        "image_digest": cfg.image_digest,
        "pages": len(pages),
        # A volume completes with failed pages recorded (the product owner,
        # 2026-09-14), so the completion marker itself has to say whether
        # every page came out. Two counts rather than a `complete` flag: a
        # reader that only wants "did it all work" compares them, and one
        # that wants the pages still has `results`, which they are derived
        # from and so can never disagree with.
        "pages_ok": len(ok_pages),
        "pages_failed": len(failed_pages),
        "results": _results_json(stats, scores),
        "source_manifest": source_manifest_url,
        # W7: which source image each page came from, so a resume after an
        # edited images: list / re-ordered manifest can tell a stale page from
        # a done one (_changed_sources). Redacted (S6): the bucket is public
        # and tokens rotate anyway.
        "page_sources": {p.name: redact_url(p.image_url) for p in pages},
        # W5: the redacted form above cannot tell two pages apart on a host
        # that selects the image by query (`?id=`), so resume compares this
        # digest of the full URL-minus-credentials instead.
        "page_source_digests": {p.name: source_digest(p.image_url) for p in pages},
        "canvas_ids": {p.name: _canvas_id(p.canvas) for p in pages},
        "max_image_width": cfg.max_image_width,
        "bytes_fetched": bytes_fetched,
        "wall_seconds": round(wall, 1),
        "gpu_stall_seconds": round(stats.stall_seconds, 1),
        "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
        "viewer_url": f"{cfg.public_results_base.rstrip('/')}"
        f"/{cfg.volume_prefix}/iiif.json",
    }
    block = (
        qp.summary(scores, pipeline_text, canvases)
        if isinstance(summary, _Unset)
        else summary
    )
    if block is not None:
        body["quality"] = block
    if image_cache is not None:
        body["image_cache"] = image_cache
    return body


class Published(NamedTuple):
    """What the publish stage tells main: whether iiif.json went out, and
    the volume's quality block (None without scores) for progress.json."""

    wrote_iiif: bool
    quality: dict | None


def run(
    cfg: Config,
    store: ResultStore,
    source_manifest: dict,
    source_manifest_url: str,
    pages: list[PageRef],
    stats: StreamStats,
    uploaded: set[str],
    t_start: float,
    bytes_fetched: int,
    image_cache: dict | None = None,
) -> Published:
    """iiif.json (when any dims resolved), pipeline.yaml, manifest.json last.
    Returns whether iiif.json was written, so main.py can tell progress.json's
    ``viewer_published`` the final publish covered it too -- a volume small
    enough that it never crossed the interim cadence (progress.py) still ends
    up saying so once it is actually done -- and the volume's quality block,
    so the final progress.json can carry it too without a second read of
    manifest.json. ``image_cache`` is the run's cache counts (imagecache.py),
    or None with the cache off."""
    dims = alto_dims(cfg, store, pages, uploaded)
    wrote_iiif = bool(dims)
    pipeline_text = Path(cfg.pipeline_path).read_text()
    canvases = [p.name for p in pages if p.name in dims]
    block = qp.summary(store.page_quality, pipeline_text, canvases)
    if dims:
        store.put_json(
            "iiif.json",
            build_viewer_manifest(
                cfg,
                source_manifest,
                pages,
                dims,
                quality=store.page_quality,
                summary=block,
            ),
        )
    store.put_text("pipeline.yaml", pipeline_text, "text/yaml")
    # Snapshotted here, not before the stage: reading stored ALTO back and
    # writing iiif.json/pipeline.yaml is time this run spent (wall_seconds
    # and pages_per_second have always covered it).
    wall = time.monotonic() - t_start
    body = run_manifest(
        cfg,
        pages,
        stats,
        source_manifest_url,
        pipeline_text,
        wall,
        bytes_fetched,
        quality=store.page_quality,
        canvases=canvases,
        summary=block,
        image_cache=image_cache,
    )
    store.put_json("manifest.json", body)
    log.info(
        "[%s] COMPLETE %d pages (%d processed, %d failed) in %.1fs, viewer: %s",
        cfg.volume_ref,
        len(pages),
        body["pages_ok"],
        body["pages_failed"],
        wall,
        body["viewer_url"],
    )
    return Published(wrote_iiif, body.get("quality"))
