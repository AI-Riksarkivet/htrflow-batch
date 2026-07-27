"""Stage wiring: setup -> resume -> stream -> verify -> publish (DESIGN.md §5.1)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from .config import Config, ConfigError
from .fetch import run_downloader
from .iiif import ManifestError, fetch_manifest, pages_from_manifest
from .store import ResultStore
from .stream import PageOutcome, consume
from .viewer import build_viewer_manifest, parse_alto_dims, parse_alto_dims_bytes

log = logging.getLogger("htrflow_batch")

EXIT_OK = 0
EXIT_PERMANENT = 13
EXIT_TRANSIENT = 1


class SetupError(Exception):
    """Permanent config/setup failure -> EXIT_PERMANENT."""


def _http_client() -> httpx.Client:
    return httpx.Client()


def _terminate(env: Mapping[str, str], reason: dict) -> None:
    path = env.get("TERMINATION_LOG_PATH", "/dev/termination-log")
    try:
        Path(path).write_text(json.dumps(reason)[:4096])
    except OSError:
        log.warning("could not write termination log to %s", path)


def _default_factory(cfg: Config):
    from . import driver  # htrflow imports stay function-local

    out_dir = Path(cfg.workdir) / "outputs"
    pipeline = driver.load_pipeline(cfg.pipeline_path, out_dir)

    def process(image_path: Path):
        return driver.process_page(pipeline, image_path, out_dir)

    return process


def main(env: Optional[Mapping[str, str]] = None,
         process_page_factory: Optional[Callable] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    env = dict(env if env is not None else os.environ)
    t_start = time.monotonic()
    stage = "setup"
    try:
        cfg = Config.from_env(env)
        store = ResultStore(cfg)
        workdir = Path(cfg.workdir)
        input_dir = workdir / "input"
        client = _http_client()

        # -- stage 1: setup -------------------------------------------------
        source_manifest = fetch_manifest(cfg.manifest_url, client)
        pages = pages_from_manifest(source_manifest, cfg.max_image_width)
        if cfg.max_pages:
            pages = pages[: cfg.max_pages]
        log.info("[%s] %d pages in manifest", cfg.volume_ref, len(pages))

        # factory AFTER downloader start would be ideal (overlap, §5.6) but
        # correctness first: start downloads, then load models concurrently.
        # -- stage 2: resume -------------------------------------------------
        done = store.done_pages() if cfg.resume else set()
        todo = [p for p in pages if p.name not in done]
        log.info("[%s] resume: %d done, %d to process",
                 cfg.volume_ref, len(done), len(todo))

        # -- stage 3: streaming loop ------------------------------------------
        stage = "stream"
        out_q: queue.Queue = queue.Queue()
        slots = threading.Semaphore(cfg.lookahead_pages)
        bytes_box = {}

        def dl():
            bytes_box["n"] = run_downloader(
                todo, input_dir, out_q, slots, client,
                concurrency=cfg.download_concurrency)

        dl_thread = threading.Thread(target=dl, daemon=True, name="downloader")
        dl_thread.start()

        # model load overlaps first downloads (DESIGN.md §5.6)
        factory = process_page_factory or _default_factory
        process = factory(cfg)

        stats = consume(out_q, slots, process, store.upload_page)
        dl_thread.join()
        for p in pages:
            if p.name in done:
                stats.results[p.name] = PageOutcome("skipped")

        # -- stage 4: verify (D8) ---------------------------------------------
        stage = "verify"
        uploaded = store.uploaded_pages()
        expected = {p.name for p in pages}
        missing = sorted(expected - uploaded)
        failed = sorted(n for n, r in stats.results.items()
                        if r.status == "failed")
        if missing or failed:
            raise RuntimeError(
                f"verify failed: missing={missing} failed={failed}")

        # -- stage 5: publish (iiif.json, pipeline.yaml, manifest.json LAST) --
        stage = "publish"
        dims = {}
        out_dir = Path(cfg.workdir) / "outputs"
        for p in pages:
            alto = sorted((out_dir / "alto").glob(f"**/{p.name}*.xml")) \
                if (out_dir / "alto").exists() else []
            if alto:
                try:
                    dims[p.name] = parse_alto_dims(alto[0])
                except ValueError:
                    pass
            elif p.name in uploaded:
                # resumed/skipped page: no local ALTO from this run, but a
                # prior run already published one — fetch it to fill dims so
                # the viewer manifest stays complete across resumes.
                try:
                    data = store.get_bytes(f"alto/{p.name}.xml")
                    dims[p.name] = parse_alto_dims_bytes(data)
                except Exception:
                    pass
        if dims:
            store.put_json("iiif.json", build_viewer_manifest(
                cfg, source_manifest, pages, dims))
        pipeline_text = Path(cfg.pipeline_path).read_text()
        store.put_text("pipeline.yaml", pipeline_text, "text/yaml")

        wall = time.monotonic() - t_start
        ok_pages = [n for n, r in stats.results.items() if r.status == "ok"]
        viewer_url = (f"{cfg.public_results_base.rstrip('/')}/"
                      f"{cfg.volume_prefix}/iiif.json")
        store.put_json("manifest.json", {
            "volume": cfg.volume_ref,
            "pipeline_id": cfg.pipeline_id,
            "pipeline_sha256": hashlib.sha256(pipeline_text.encode()).hexdigest(),
            "pipeline_yaml": pipeline_text,
            "htrflow_version": _htrflow_version(),
            "image_digest": env.get("IMAGE_DIGEST", "unknown"),
            "pages": len(pages),
            "results": {n: {"status": r.status, "seconds": round(r.seconds, 2),
                            **({"error": r.error} if r.error else {})}
                        for n, r in sorted(stats.results.items())},
            "source_manifest": cfg.manifest_url,
            "max_image_width": cfg.max_image_width,
            "bytes_fetched": bytes_box.get("n", 0),
            "wall_seconds": round(wall, 1),
            "gpu_stall_seconds": round(stats.stall_seconds, 1),
            "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
            "viewer_url": viewer_url,
        })
        log.info("[%s] COMPLETE %d pages (%d processed) in %.1fs, viewer: %s",
                 cfg.volume_ref, len(pages), len(ok_pages), wall, viewer_url)
        shutil.rmtree(workdir, ignore_errors=True)
        return EXIT_OK

    except (ConfigError, ManifestError, SetupError, ValueError) as e:
        log.error("permanent failure in %s: %s", stage, e)
        _terminate(env, {"stage": stage, "permanent": True, "error": str(e)})
        return EXIT_PERMANENT
    except Exception as e:
        log.error("transient failure in %s: %s\n%s", stage, e,
                  traceback.format_exc())
        _terminate(env, {"stage": stage, "permanent": False, "error": str(e)})
        return EXIT_TRANSIENT


def _htrflow_version() -> str:
    try:
        from .driver import htrflow_version
        return htrflow_version()
    except Exception:
        return "unknown"
