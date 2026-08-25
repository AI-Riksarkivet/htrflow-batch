"""Stage wiring: setup -> resume -> stream -> verify -> publish (docs: wrapper)."""

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
from .stream import PageOutcome, StreamStats, consume
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
    error = reason.get("error")
    if isinstance(error, str) and len(error) > 3500:
        # Truncate the *field* before serializing, never the serialized JSON
        # itself -- slicing json.dumps(reason)[:N] can cut mid-string and
        # write invalid JSON to the termination log.
        reason = {**reason, "error": error[:3500] + "...(truncated)"}
    try:
        Path(path).write_text(json.dumps(reason))
    except OSError:
        log.warning("could not write termination log to %s", path)


#: Env vars the Job spec points into the tmpfs workdir (docs: security, D14).
#: Under readOnlyRootFilesystem these are the only places htrflow's stack may
#: write outside HF_HOME: ultralytics settings, triton/inductor JIT caches
#: (under HOME) and temp files. They must exist before any model is built.
WRITABLE_DIR_VARS = ("HOME", "TMPDIR", "YOLO_CONFIG_DIR")


def prepare_writable_dirs(env: Mapping[str, str]) -> None:
    for var in WRITABLE_DIR_VARS:
        if env.get(var):
            Path(env[var]).mkdir(parents=True, exist_ok=True)


def _default_factory(cfg: Config):
    from . import driver  # htrflow imports stay function-local

    out_dir = Path(cfg.workdir) / "outputs"
    pipeline = driver.load_pipeline(cfg.pipeline_path, out_dir)

    def process(image_path: Path):
        return driver.process_page(pipeline, image_path, out_dir)

    return process


def publish_failure_metrics(store, cfg, stats, wall: float, stage: str, error: str):
    """Best-effort: preserve run evidence when a run fails (docs: wrapper).
    Must never raise — it runs on the failure path."""
    try:
        store.put_json(
            "metrics-failed-latest.json",
            {
                "volume": cfg.volume_ref,
                "pipeline_id": cfg.pipeline_id,
                "stage": stage,
                "error": str(error)[:2000],
                "wall_seconds": round(wall, 1),
                "gpu_stall_seconds": round(stats.stall_seconds, 1),
                "results": {
                    n: {
                        "status": r.status,
                        "seconds": round(r.seconds, 2),
                        **({"error": r.error} if r.error else {}),
                    }
                    for n, r in sorted(stats.results.items())
                },
            },
        )
    except Exception:
        log.warning("could not publish failure metrics", exc_info=True)


def main(
    env: Optional[Mapping[str, str]] = None,
    process_page_factory: Optional[Callable] = None,
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    env = dict(env if env is not None else os.environ)
    t_start = time.monotonic()
    stage = "setup"
    # Bound up-front so the failure paths below can tell "we never got that
    # far" from "we have evidence worth publishing".
    cfg: Optional[Config] = None
    store: Optional[ResultStore] = None
    stats: Optional[StreamStats] = None
    try:
        cfg = Config.from_env(env)
        prepare_writable_dirs(env)
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

        # -- stage 2: resume -------------------------------------------------
        stage = "resume"
        done = store.done_pages() if cfg.resume else set()
        todo = [p for p in pages if p.name not in done]
        log.info(
            "[%s] resume: %d done, %d to process", cfg.volume_ref, len(done), len(todo)
        )

        # -- stage 3: streaming loop ------------------------------------------
        stage = "stream"
        out_q: queue.Queue = queue.Queue()
        slots = threading.Semaphore(cfg.lookahead_pages)
        bytes_box = {}

        def dl():
            # If run_downloader raises before it can enqueue its own
            # completion sentinel (e.g. dest_dir mkdir fails), consume()
            # would otherwise block forever on out_queue.get(). Catch here
            # and always push the sentinel so the stream loop is guaranteed
            # to terminate; the resulting missing pages fail the verify gate
            # below, which is the correct (transient, retryable) outcome.
            try:
                bytes_box["n"] = run_downloader(
                    todo,
                    input_dir,
                    out_q,
                    slots,
                    client,
                    concurrency=cfg.download_concurrency,
                )
            except Exception as e:
                log.error("downloader thread failed: %r", e)
                bytes_box["error"] = repr(e)
                out_q.put(None)

        dl_thread = threading.Thread(target=dl, daemon=True, name="downloader")
        dl_thread.start()

        # Build/load the process fn only after the downloader thread is
        # started, so model load overlaps the first downloads (docs: wrapper,
        # "Model handling") instead of happening serially before any bytes move.
        factory = process_page_factory or _default_factory
        process = factory(cfg)

        stats = consume(out_q, slots, process, store.upload_page)
        dl_thread.join()
        for p in pages:
            if p.name in done:
                stats.results[p.name] = PageOutcome(status="skipped")

        # -- stage 4: verify (D8) ---------------------------------------------
        stage = "verify"
        uploaded = store.uploaded_pages()
        expected = {p.name for p in pages}
        missing = sorted(expected - uploaded)
        failed = sorted(n for n, r in stats.results.items() if r.status == "failed")
        if missing or failed:
            raise RuntimeError(f"verify failed: missing={missing} failed={failed}")

        # -- stage 5: publish (iiif.json, pipeline.yaml, manifest.json LAST) --
        stage = "publish"
        dims = {}
        out_dir = Path(cfg.workdir) / "outputs"
        for p in pages:
            alto = (
                sorted((out_dir / "alto").glob(f"**/{p.name}*.xml"))
                if (out_dir / "alto").exists()
                else []
            )
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
        if len(dims) < len(pages):
            log.warning("viewer manifest covers %d/%d pages", len(dims), len(pages))
        if not dims:
            log.warning(
                "[%s] no ALTO dims resolved for any page; "
                "iiif.json not published, viewer_url will 404",
                cfg.volume_ref,
            )
        if dims:
            store.put_json(
                "iiif.json", build_viewer_manifest(cfg, source_manifest, pages, dims)
            )
        pipeline_text = Path(cfg.pipeline_path).read_text()
        store.put_text("pipeline.yaml", pipeline_text, "text/yaml")

        wall = time.monotonic() - t_start
        ok_pages = [n for n, r in stats.results.items() if r.status == "ok"]
        viewer_url = (
            f"{cfg.public_results_base.rstrip('/')}/{cfg.volume_prefix}/iiif.json"
        )
        store.put_json(
            "manifest.json",
            {
                "volume": cfg.volume_ref,
                "pipeline_id": cfg.pipeline_id,
                "pipeline_sha256": hashlib.sha256(pipeline_text.encode()).hexdigest(),
                "pipeline_yaml": pipeline_text,
                "htrflow_version": _htrflow_version(),
                "image_digest": env.get("IMAGE_DIGEST", "unknown"),
                "pages": len(pages),
                "results": {
                    n: {
                        "status": r.status,
                        "seconds": round(r.seconds, 2),
                        **({"error": r.error} if r.error else {}),
                    }
                    for n, r in sorted(stats.results.items())
                },
                "source_manifest": cfg.manifest_url,
                "max_image_width": cfg.max_image_width,
                "bytes_fetched": bytes_box.get("n", 0),
                "wall_seconds": round(wall, 1),
                "gpu_stall_seconds": round(stats.stall_seconds, 1),
                "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
                "viewer_url": viewer_url,
            },
        )
        log.info(
            "[%s] COMPLETE %d pages (%d processed) in %.1fs, viewer: %s",
            cfg.volume_ref,
            len(pages),
            len(ok_pages),
            wall,
            viewer_url,
        )
        # Only clean up on success; on any failure path below, the workdir
        # (downloaded images, local ALTO/PAGE outputs) is intentionally left
        # in place for postmortem inspection.
        shutil.rmtree(workdir, ignore_errors=True)
        return EXIT_OK

    except (ConfigError, ManifestError, SetupError, ValueError) as e:
        log.error("permanent failure in %s: %s", stage, e)
        _terminate(env, {"stage": stage, "permanent": True, "error": str(e)})
        _publish_failure(cfg, store, stats, t_start, stage, e)
        return EXIT_PERMANENT
    except Exception as e:
        log.error("transient failure in %s: %s\n%s", stage, e, traceback.format_exc())
        _terminate(env, {"stage": stage, "permanent": False, "error": str(e)})
        _publish_failure(cfg, store, stats, t_start, stage, e)
        return EXIT_TRANSIENT


def _publish_failure(cfg, store, stats, t_start: float, stage: str, e: BaseException):
    """Skip setup-stage failures, where there is no store/stats to report on.

    Runs *after* _terminate() on purpose: the termination log is a local
    write_text that is effectively instant, while this S3 upload can hang for
    minutes on default boto timeouts — especially when S3 is itself the reason
    the run failed. Ordering it last means a stuck bucket cannot cost us the
    kubernetes termination message.
    """
    if cfg is not None and store is not None and stats is not None:
        publish_failure_metrics(
            store, cfg, stats, time.monotonic() - t_start, stage, str(e)
        )


def _htrflow_version() -> str:
    try:
        from .driver import htrflow_version

        return htrflow_version()
    except Exception:
        return "unknown"
