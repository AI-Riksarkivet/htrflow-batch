"""Stage wiring: setup -> resume -> stream -> verify -> publish (docs: wrapper)."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import queue
import shutil
import signal
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from .config import Config, ConfigError
from .fetch import run_downloader
from .iiif import (
    ManifestError,
    fetch_manifest,
    pages_from_manifest,
    redact_url,
    redact_urls,
)
from .logship import LogCapture
from .store import ResultStore
from .stream import PageOutcome, StreamStats, consume
from .viewer import build_viewer_manifest, parse_alto_dims, parse_alto_dims_bytes

log = logging.getLogger("htrflow_batch")

EXIT_OK = 0
EXIT_PERMANENT = 13
EXIT_TRANSIENT = 1
EXIT_SIGTERM = 143  # 128 + SIGTERM, what an unhandled kill would report


class SetupError(Exception):
    """Permanent config/setup failure -> EXIT_PERMANENT."""


class Terminated(BaseException):
    """Raised in the main thread by the SIGTERM handler. BaseException on
    purpose: stream.consume records any Exception as a failed page and
    carries on, and this must unwind straight through it (and through any
    lock the interrupted frame holds) to main()."""


class RunState:
    """What the signal handler needs to see: the stage the run is in."""

    stage: str = "setup"


def _set_signal(signum: int, handler):
    """signal.signal only works in the main thread; elsewhere (embedded,
    tests in a worker) run without a handler rather than fail."""
    try:
        return signal.signal(signum, handler)
    except ValueError:
        return None


def _hard_exit(code: int) -> None:
    """Exit NOW. sys.exit would wait for the downloader's ThreadPoolExecutor
    workers (joined at interpreter shutdown) — a download stuck in its 120 s
    timeout would run the pod into the SIGKILL instead of a clean 143."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def _http_client() -> httpx.Client:
    # S5: campaign data drives these fetches; bound redirect chains too.
    return httpx.Client(max_redirects=5)


def _terminate(env: Mapping[str, str], reason: dict) -> None:
    path = env.get("TERMINATION_LOG_PATH", "/dev/termination-log")
    error = reason.get("error")
    if isinstance(error, str):
        reason = {**reason, "error": redact_urls(error)}  # S6: world-readable
        error = reason["error"]
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


def _results_json(stats: StreamStats) -> dict:
    """Per-page outcomes for manifest.json / metrics-failed-latest.json;
    error strings lose URL secrets (S6)."""
    return {
        n: {
            "status": r.status,
            "seconds": round(r.seconds, 2),
            **({"error": redact_urls(r.error)} if r.error else {}),
        }
        for n, r in sorted(stats.results.items())
    }


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
                "error": redact_urls(str(error))[:2000],
                "wall_seconds": round(wall, 1),
                "gpu_stall_seconds": round(stats.stall_seconds, 1),
                "results": _results_json(stats),
            },
        )
    except Exception:
        log.warning("could not publish failure metrics", exc_info=True)


def main(
    env: Optional[Mapping[str, str]] = None,
    process_page_factory: Optional[Callable] = None,
) -> int:
    # Tee stdout/stderr BEFORE logging binds its stream, so the shipped run
    # log carries htrflow's logging output and its bare prints alike
    # (docs: wrapper, "Live run log"). finish() always restores the streams
    # and does the final upload, on every exit path.
    env = dict(env if env is not None else os.environ)
    state = RunState()
    capture = LogCapture.install()

    def on_sigterm(signum, frame):
        raise Terminated()

    previous = _set_signal(signal.SIGTERM, on_sigterm)
    try:
        return _main(env, process_page_factory, capture, state)
    except Terminated:
        # O2: the Job deadline or a node drain. Leave the same evidence a
        # failure would (termination message + complete run log), then exit
        # 143 so the reconciler classifies it as a retry, not exit 13.
        log.error("SIGTERM in stage %s: shutting down", state.stage)
        _terminate(env, {"stage": state.stage, "permanent": False, "error": "SIGTERM"})
        capture.finish()
        _hard_exit(EXIT_SIGTERM)
        return EXIT_SIGTERM  # reached only when _hard_exit is stubbed (tests)
    finally:
        if previous is not None:
            _set_signal(signal.SIGTERM, previous)
        capture.finish()


def _main(
    env: Mapping[str, str],
    process_page_factory: Optional[Callable],
    capture: LogCapture,
    state: RunState,
) -> int:
    capture.attach_logging()  # not basicConfig: see LogCapture.attach_logging
    t_start = time.monotonic()
    # Bound up-front so the failure paths below can tell "we never got that
    # far" from "we have evidence worth publishing".
    cfg: Optional[Config] = None
    store: Optional[ResultStore] = None
    stats: Optional[StreamStats] = None
    # W10: set on every failure path so queued downloads stop short instead
    # of holding the interpreter (executor workers are joined at exit).
    stop = threading.Event()
    try:
        cfg = Config.from_env(env)
        prepare_writable_dirs(env)
        store = ResultStore(cfg)
        capture.start_shipping(store.put_run_log, cfg.log_ship_seconds)
        workdir = Path(cfg.workdir)
        input_dir = workdir / "input"
        client = _http_client()

        # -- stage 1: setup -------------------------------------------------
        source_manifest = fetch_manifest(
            cfg.manifest_url, client, max_bytes=cfg.manifest_max_bytes
        )
        pages = pages_from_manifest(source_manifest, cfg.max_image_width)
        if cfg.max_pages:
            pages = pages[: cfg.max_pages]
        log.info("[%s] %d pages in manifest", cfg.volume_ref, len(pages))

        # -- stage 2: resume -------------------------------------------------
        state.stage = "resume"
        done = store.done_pages() if cfg.resume else set()
        changed = _changed_sources(store, pages, done) if done else set()
        if changed:
            log.info(
                "[%s] resume: %d done pages have a new source image, reprocessing",
                cfg.volume_ref,
                len(changed),
            )
            done -= changed
        todo = [p for p in pages if p.name not in done]
        log.info(
            "[%s] resume: %d done, %d to process", cfg.volume_ref, len(done), len(todo)
        )

        # -- stage 3: streaming loop ------------------------------------------
        state.stage = "stream"
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
                    max_bytes=cfg.fetch_max_bytes,
                    stop=stop,
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
        state.stage = "load"  # W9: a model-load failure is not a stream failure
        factory = process_page_factory or _default_factory
        process = factory(cfg)

        state.stage = "stream"
        # The stream unlinks each page image once processed (memory budget),
        # so the thumbnail is cut from the first page as it goes by.
        thumb_box: dict[str, Optional[str]] = {}

        def process_with_thumb(image_path: Path):
            if "key" not in thumb_box:
                thumb_box["key"] = make_thumbnail(store, image_path)
            return process(image_path)

        stats = consume(out_q, slots, process_with_thumb, store.upload_page)
        dl_thread.join()
        for p in pages:
            if p.name in done:
                stats.results[p.name] = PageOutcome(status="skipped")

        # -- stage 4: verify (D8) ---------------------------------------------
        state.stage = "verify"
        uploaded = store.uploaded_pages()
        expected = {p.name for p in pages}
        missing = sorted(expected - uploaded)
        failed = sorted(n for n, r in stats.results.items() if r.status == "failed")
        if missing or failed:
            raise RuntimeError(f"verify failed: missing={missing} failed={failed}")

        # -- stage 5: publish (iiif.json, pipeline.yaml, manifest.json LAST) --
        state.stage = "publish"
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
                except (ValueError, ET.ParseError):
                    pass
            elif p.name in uploaded:
                # resumed/skipped page: no local ALTO from this run, but a
                # prior run already published one — fetch it to fill dims so
                # the viewer manifest stays complete across resumes.
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
        if dims:
            store.put_json(
                "iiif.json", build_viewer_manifest(cfg, source_manifest, pages, dims)
            )
        pipeline_text = Path(cfg.pipeline_path).read_text()
        store.put_text("pipeline.yaml", pipeline_text, "text/yaml")

        thumb_key = thumb_box.get("key") or previous_thumbnail(store)

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
                "results": _results_json(stats),
                "source_manifest": cfg.manifest_url,
                # W7: which source image each page came from, so a resume
                # after an edited images: list / re-ordered manifest can tell
                # a stale page from a done one (_changed_sources). Redacted
                # (S6): the bucket is public and tokens rotate anyway.
                "page_sources": {p.name: redact_url(p.image_url) for p in pages},
                "canvas_ids": {p.name: _canvas_id(p.canvas) for p in pages},
                "max_image_width": cfg.max_image_width,
                "bytes_fetched": bytes_box.get("n", 0),
                "wall_seconds": round(wall, 1),
                "gpu_stall_seconds": round(stats.stall_seconds, 1),
                "pages_per_second": round(len(ok_pages) / wall, 3) if wall else 0,
                "viewer_url": viewer_url,
                # Relative key of the first-page picture the campaign browser
                # shows (None when no page could be decoded); the reconciler
                # reads it once per finished volume.
                "thumbnail": thumb_key,
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
        stop.set()
        stage = state.stage
        log.error("permanent failure in %s: %s", stage, e)
        _terminate(env, {"stage": stage, "permanent": True, "error": str(e)})
        _publish_failure(cfg, store, stats, t_start, stage, e)
        return EXIT_PERMANENT
    except Exception as e:
        stop.set()
        stage = state.stage
        log.error("transient failure in %s: %s\n%s", stage, e, traceback.format_exc())
        _terminate(env, {"stage": stage, "permanent": False, "error": str(e)})
        _publish_failure(cfg, store, stats, t_start, stage, e)
        return EXIT_TRANSIENT


def _canvas_id(canvas: dict) -> str | None:
    cid = canvas.get("id") or canvas.get("@id")
    return cid if isinstance(cid, str) else None


def _changed_sources(store: ResultStore, pages, done: set[str]) -> set[str]:
    """Done pages whose image URL differs from the one the previous completed
    run recorded in manifest.json (W7). No previous manifest, or one without
    page_sources (older wrapper), means nothing to compare: keep them done."""
    previous = store.get_json_or_none("manifest.json")
    sources = (previous or {}).get("page_sources")
    if not isinstance(sources, dict):
        return set()
    return {
        p.name
        for p in pages
        if p.name in done and p.name in sources and sources[p.name] != p.image_url
    }


THUMB_KEY = "thumb.jpg"
THUMB_WIDTH = 200


def make_thumbnail(store, image_path: Path) -> Optional[str]:
    """Best-effort ``thumb.jpg`` cut from one page image.

    The campaign browser paints a 26 px picture per volume; serving the
    source image for that costs megabytes per row (audit F2), and volumes
    declared as ``images:`` have no IIIF service to size from. Pillow comes
    with the htrflow base image but is not a wrapper dependency, so a missing
    import (or an undecodable file) only means "no picture". Never raises:
    the transcription is the job, the picture is a nicety.
    """
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not available; no thumbnail")
        return None
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            ratio = THUMB_WIDTH / img.width
            img = img.resize((THUMB_WIDTH, max(1, round(img.height * ratio))))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
        store.put_bytes(THUMB_KEY, buf.getvalue(), "image/jpeg")
        return THUMB_KEY
    except Exception as e:
        log.warning("thumbnail from %s failed: %r", image_path.name, e)
        return None


def previous_thumbnail(store) -> Optional[str]:
    """A resumed run that processed nothing keeps the thumbnail the previous
    run recorded in manifest.json."""
    previous = store.get_json_or_none("manifest.json")
    if isinstance(previous, dict) and previous.get("thumbnail"):
        return str(previous["thumbnail"])
    return None


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
