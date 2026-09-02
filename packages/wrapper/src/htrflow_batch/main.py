"""Stage wiring: setup -> resume -> stream -> verify -> publish (docs: wrapper)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from . import publish
from .config import Config, ConfigError
from .iiif import (
    ManifestError,
    PageRef,
    check_http_url,
    fetch_manifest,
    pages_from_manifest,
    redact_url,
    redact_urls,
)
from .logship import LogCapture
from .store import ResultStore
from .stream import PageOutcome, PageStream, StreamStats, consume
from .synthetic import build_manifest

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
    """Stage, plus `terminating`: success/failure/SIGTERM/MAX_SECONDS race for it."""

    def __init__(self) -> None:
        self.stage = "setup"
        self.terminating = threading.Lock()


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


def _terminate(env: Mapping[str, str], reason: dict, state: "RunState") -> bool:
    """Writes the log iff it wins state.terminating; returns whether it wrote."""
    if not state.terminating.acquire(blocking=False):
        return False
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
    return True


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
        # 143 so Kubernetes retries the index like exit 1, not FailIndex.
        # Same shape as the other failure lines: the run viewer's terminal-line
        # regex (frontend runlog.ts) is the contract that stops live polling.
        log.error("transient failure in %s: SIGTERM, shutting down", state.stage)
        reason = {"stage": state.stage, "permanent": False, "error": "SIGTERM"}
        if not _terminate(env, reason, state):
            return EXIT_TRANSIENT  # MAX_SECONDS already won the race
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
    """setup->resume->stream->verify->publish, under the MAX_SECONDS watchdog."""
    capture.attach_logging()  # not basicConfig: see LogCapture.attach_logging
    t_start = time.monotonic()
    # W10: set on every failure path so queued downloads stop short instead
    # of holding the interpreter (executor workers are joined at exit).
    stop = threading.Event()
    timer: Optional[threading.Timer] = None
    try:
        cfg = Config.from_env(env)
        prepare_writable_dirs(env)
        store = ResultStore(cfg)
        capture.start_shipping(store.put_run_log, cfg.log_ship_seconds)
        timer = _start_max_seconds_timer(cfg, env, state, capture)
        client = _http_client()

        source, source_url, pages = _setup(cfg, client, store, state)
        todo, done = _resume(cfg, store, pages, state)
        stats, nbytes = _stream(
            cfg, client, store, todo, done, process_page_factory, state, stop
        )
        uploaded = _verify(store, pages, stats, state)
        state.stage = "publish"
        publish.run(
            cfg, env, store, source, source_url, pages, stats, uploaded, t_start, nbytes
        )

        # Only clean up on success; on any failure path below, the workdir
        # (downloaded images, local ALTO/PAGE outputs) is intentionally left
        # in place for postmortem inspection.
        shutil.rmtree(cfg.workdir, ignore_errors=True)
        if not state.terminating.acquire(blocking=False):
            return EXIT_TRANSIENT  # MAX_SECONDS already won the race
        return EXIT_OK
    except OSError as e:
        # An I/O condition is never a config mistake, even when it is also a
        # ValueError: huggingface_hub's LocalEntryNotFoundError subclasses
        # FileNotFoundError AND ValueError, and under HF_HUB_OFFLINE=1 it is
        # what a model missing from the read-only cache raises. A re-warm and
        # a retry fix that, so it must not FailIndex the volume (this is also
        # driver.build_pipeline's "an OSError from model construction stays
        # transient" contract).
        return _transient(env, state, stop, e)
    except (ConfigError, ManifestError, SetupError, ValueError) as e:
        stop.set()
        stage = state.stage
        log.error("permanent failure in %s: %s", stage, e)
        reason = {"stage": stage, "permanent": True, "error": str(e)}
        return EXIT_PERMANENT if _terminate(env, reason, state) else EXIT_TRANSIENT
    except Exception as e:
        return _transient(env, state, stop, e)
    finally:
        if timer is not None:
            timer.cancel()


def _transient(
    env: Mapping[str, str], state: RunState, stop: threading.Event, e: BaseException
) -> int:
    """Retryable: stop the queued downloads (W10), leave the evidence, exit 1."""
    stop.set()
    log.error("transient failure in %s: %s\n%s", state.stage, e, traceback.format_exc())
    _terminate(env, {"stage": state.stage, "permanent": False, "error": str(e)}, state)
    return EXIT_TRANSIENT


def _setup(
    cfg: Config, client: httpx.Client, store: ResultStore, state: RunState
) -> tuple[dict, str, list[PageRef]]:
    """The source manifest — fetched, or synthesized from IMAGES and published
    under sources/ — its URL, and the ordered pages it enumerates."""
    state.stage = "setup"
    if cfg.images:
        source, url = _synthetic_source(cfg, store)
    else:
        source = fetch_manifest(
            cfg.manifest_url, client, max_bytes=cfg.manifest_max_bytes
        )
        url = cfg.manifest_url
    pages = pages_from_manifest(source, cfg.max_image_width)
    if cfg.max_pages:
        pages = pages[: cfg.max_pages]
    log.info("[%s] %d pages in manifest", cfg.volume_ref, len(pages))
    return source, url, pages


def _resume(
    cfg: Config, store: ResultStore, pages: list[PageRef], state: RunState
) -> tuple[list[PageRef], set[str]]:
    """The pages left to process, and the done ones (skipped in the results).
    A page is done only when both formats are in S3 and its source image URL
    is the one the last completed run recorded (W7)."""
    state.stage = "resume"
    done = store.done_pages() if cfg.resume else set()
    done &= {p.name for p in pages}  # S3 can hold pages this run does not cover
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
    return todo, done


def _stream(
    cfg: Config,
    client: httpx.Client,
    store: ResultStore,
    todo: list[PageRef],
    done: set[str],
    factory: Optional[Callable],
    state: RunState,
    stop: threading.Event,
) -> tuple[StreamStats, int]:
    """Download ∥ process ∥ upload, never more than LOOKAHEAD_PAGES ahead:
    the per-page outcomes and the bytes fetched."""
    state.stage = "stream"
    stream = PageStream(
        todo,
        Path(cfg.workdir) / "input",
        client,
        lookahead=cfg.lookahead_pages,
        concurrency=cfg.download_concurrency,
        max_bytes=cfg.fetch_max_bytes,
        stop=stop,
    )
    try:
        # The first downloads are in flight, so the model load overlaps them
        # (docs: wrapper, "Model handling") instead of preceding them.
        state.stage = "load"  # W9: a model-load failure is not a stream failure
        process = (factory or _default_factory)(cfg)

        state.stage = "stream"
        stats = consume(stream, process, store.upload_page)
    finally:
        stream.close()  # never blocks; cancels what is still queued
    for name in done:
        stats.results[name] = PageOutcome(status="skipped")
    return stats, stream.bytes_fetched


def _verify(
    store: ResultStore, pages: list[PageRef], stats: StreamStats, state: RunState
) -> set[str]:
    """D8: every page has PAGE and ALTO in S3 and none is marked failed. A gap
    is transient — Kubernetes retries the index and resume converges — and the
    missing/failed lists go in the termination message. Returns what is stored,
    which publish reads back for the pages this run skipped."""
    state.stage = "verify"
    uploaded = store.uploaded_pages()
    missing = sorted({p.name for p in pages} - uploaded)
    failed = sorted(n for n, r in stats.results.items() if r.status == "failed")
    if missing or failed:
        detail = _failure_detail(stats, failed)
        raise RuntimeError(f"verify failed: missing={missing} failed={failed}{detail}")
    return uploaded


#: How much of the failed pages' errors goes in the verify message: enough to
#: name the cause, little enough to stay inside _terminate's 3500-char field
#: cap (and the 4 KiB the kubelet keeps of a termination message).
FAILED_DETAIL_PAGES = 10
FAILED_DETAIL_CHARS = 200


def _failure_detail(stats: StreamStats, failed: list[str]) -> str:
    """Why those pages failed. Without it the operator reads
    "verify failed: failed=['0042']" and has nothing else: a run with failed
    pages never publishes manifest.json, where the errors would have gone."""
    if not failed:
        return ""
    shown = "; ".join(
        f"{n}: {(stats.results[n].error or '')[:FAILED_DETAIL_CHARS]}"
        for n in failed[:FAILED_DETAIL_PAGES]
    )
    rest = len(failed) - FAILED_DETAIL_PAGES
    return f" errors: {shown}" + (f" (+{rest} more)" if rest > 0 else "")


def _start_max_seconds_timer(
    cfg: Config, env: Mapping[str, str], state: RunState, capture: LogCapture
) -> Optional[threading.Timer]:
    """MAX_SECONDS: a per-volume wall-clock budget (docs: wrapper). Fires in
    its own thread; _hard_exit (os._exit) kills the process outright from
    any thread, so on_expiry need not unwind the main thread itself."""
    if cfg.max_seconds <= 0:
        return None

    def on_expiry() -> None:
        reason = {"stage": state.stage, "permanent": False, "error": "MAX_SECONDS"}
        if not _terminate(env, reason, state):
            return  # success/failure/SIGTERM already won the race
        log.error("transient failure in %s: MAX_SECONDS exceeded", state.stage)
        capture.finish()
        _hard_exit(EXIT_TRANSIENT)

    timer = threading.Timer(cfg.max_seconds, on_expiry)
    timer.daemon = True
    timer.start()
    return timer


def _synthetic_source(cfg: Config, store: ResultStore) -> tuple[dict, str]:
    """IMAGES: build and publish the synthetic P3 manifest to
    sources/<pipeline>/<volume>/manifest.json (S3_PREFIX honoured, docs:
    wrapper), then hand it back as if it had been fetched."""
    urls = [u for u in cfg.images.split(",") if u]
    for u in urls:
        check_http_url(u, "IMAGES URL")
    key = f"sources/{cfg.pipeline_id}/{cfg.volume_ref}/manifest.json"
    manifest_id = f"{cfg.public_results_base.rstrip('/')}/{cfg.root_key(key)}"
    doc = build_manifest(cfg.volume_ref, urls, manifest_id)
    store.put_json_at(key, doc)
    return doc, manifest_id


def _changed_sources(store: ResultStore, pages, done: set[str]) -> set[str]:
    """Done pages whose image URL differs from the one the previous completed
    run recorded in manifest.json (W7). No previous manifest, or one without
    page_sources (older wrapper), means nothing to compare: keep them done.
    publish stores page_sources REDACTED (S6), so compare redacted: a tokenised
    URL otherwise differs from its stored form on every retry, forever."""
    previous = store.get_json_or_none("manifest.json")
    sources = (previous or {}).get("page_sources")
    if not isinstance(sources, dict):
        return set()
    return {
        p.name
        for p in pages
        if p.name in done
        and p.name in sources
        and sources[p.name] != redact_url(p.image_url)
    }
