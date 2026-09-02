"""The publish stage (docs: wrapper): the viewer manifest, the pipeline copy,
and manifest.json LAST — its presence is the sole completion marker."""

from __future__ import annotations

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping

from .config import Config
from .iiif import PageRef, redact_url, redact_urls
from .store import ResultStore
from .stream import StreamStats
from .viewer import build_viewer_manifest, parse_alto_dims, parse_alto_dims_bytes

log = logging.getLogger("htrflow_batch")


def alto_dims(
    cfg: Config, store: ResultStore, pages: list[PageRef], uploaded: set[str]
) -> dict:
    """Page dimensions as actually processed, read back from the ALTO — the
    local file this run wrote, or the stored one for a page a previous run
    published, so a resumed volume's viewer manifest stays complete. A page
    whose ALTO will not parse is left out rather than failing the publish."""
    dims: dict[str, tuple[int, int]] = {}
    alto_dir = Path(cfg.workdir) / "outputs" / "alto"
    for p in pages:
        alto = sorted(alto_dir.glob(f"**/{p.name}*.xml")) if alto_dir.exists() else []
        if alto:
            try:
                dims[p.name] = parse_alto_dims(alto[0])
            except (ValueError, ET.ParseError):
                pass
        elif p.name in uploaded:
            try:
                data = store.get_bytes(f"alto/{p.name}.xml")
                dims[p.name] = parse_alto_dims_bytes(data)
            except (ValueError, ET.ParseError):
                pass
            except Exception:
                log.warning("could not read stored ALTO for %s", p.name)
    if len(dims) < len(pages):
        log.warning("viewer manifest covers %d/%d pages", len(dims), len(pages))
    if not dims:
        log.warning(
            "[%s] no ALTO dims resolved for any page; "
            "iiif.json not published, viewer_url will 404",
            cfg.volume_ref,
        )
    return dims


def _results_json(stats: StreamStats) -> dict:
    """Per-page outcomes for manifest.json; error strings lose URL secrets (S6)."""
    return {
        n: {
            "status": r.status,
            "seconds": round(r.seconds, 2),
            **({"error": redact_urls(r.error)} if r.error else {}),
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


def run_manifest(
    cfg: Config,
    env: Mapping[str, str],
    pages: list[PageRef],
    stats: StreamStats,
    source_manifest_url: str,
    pipeline_text: str,
    wall: float,
    bytes_fetched: int,
) -> dict:
    """The manifest.json body: what the volume is, what produced it, what came
    out, and what a resume or the Phase 2 gate reads back (docs: s3-layout)."""
    ok_pages = [n for n, r in stats.results.items() if r.status == "ok"]
    return {
        "volume": cfg.volume_ref,
        "pipeline_id": cfg.pipeline_id,
        "pipeline_sha256": hashlib.sha256(pipeline_text.encode()).hexdigest(),
        "pipeline_yaml": pipeline_text,
        "htrflow_version": _htrflow_version(),
        "image_digest": env.get("IMAGE_DIGEST", "unknown"),
        "pages": len(pages),
        "results": _results_json(stats),
        "source_manifest": source_manifest_url,
        # W7: which source image each page came from, so a resume after an
        # edited images: list / re-ordered manifest can tell a stale page from
        # a done one (_changed_sources). Redacted (S6): the bucket is public
        # and tokens rotate anyway.
        "page_sources": {p.name: redact_url(p.image_url) for p in pages},
        "canvas_ids": {p.name: _canvas_id(p.canvas) for p in pages},
        "max_image_width": cfg.max_image_width,
        "bytes_fetched": bytes_fetched,
        "wall_seconds": round(wall, 1),
        "gpu_stall_seconds": round(stats.stall_seconds, 1),
        "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
        "viewer_url": f"{cfg.public_results_base.rstrip('/')}"
        f"/{cfg.volume_prefix}/iiif.json",
    }


def run(
    cfg: Config,
    env: Mapping[str, str],
    store: ResultStore,
    source_manifest: dict,
    source_manifest_url: str,
    pages: list[PageRef],
    stats: StreamStats,
    uploaded: set[str],
    t_start: float,
    bytes_fetched: int,
) -> None:
    """iiif.json (when any dims resolved), pipeline.yaml, manifest.json last."""
    dims = alto_dims(cfg, store, pages, uploaded)
    if dims:
        store.put_json(
            "iiif.json", build_viewer_manifest(cfg, source_manifest, pages, dims)
        )
    pipeline_text = Path(cfg.pipeline_path).read_text()
    store.put_text("pipeline.yaml", pipeline_text, "text/yaml")
    # Snapshotted here, not before the stage: reading stored ALTO back and
    # writing iiif.json/pipeline.yaml is time this run spent (wall_seconds
    # and pages_per_second have always covered it).
    wall = time.monotonic() - t_start
    body = run_manifest(
        cfg, env, pages, stats, source_manifest_url, pipeline_text, wall, bytes_fetched
    )
    store.put_json("manifest.json", body)
    log.info(
        "[%s] COMPLETE %d pages (%d processed) in %.1fs, viewer: %s",
        cfg.volume_ref,
        len(pages),
        sum(1 for r in stats.results.values() if r.status == "ok"),
        wall,
        body["viewer_url"],
    )
