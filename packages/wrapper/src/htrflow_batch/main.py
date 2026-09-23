"""Stage wiring: setup -> resume -> stream -> verify -> publish (docs: wrapper)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Mapping, Optional

import httpx

from . import bounded, provenance, publish
from .config import Config, ConfigError
from .iiif import (
    ManifestError,
    PageRef,
    check_http_url,
    fetch_manifest,
    pages_from_manifest,
    redact_url,
    redact_urls,
    source_digest,
)
from .logship import LogCapture
from .progress import Progress
from .store import ResultStore
from .stream import PageOutcome, PageStream, StreamStats, Unrecoverable, consume
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
    """The stage the run is in, for the failure messages and the run log.

    A property rather than a plain attribute so that the one place a stage is
    published from (progress.py, which writes it to the bucket) does not have
    to be called at each of the eight assignments below -- and cannot be
    forgotten at the ninth."""

    def __init__(self) -> None:
        self._stage = "setup"
        self.on_change: Callable[[str], None] = lambda _stage: None
        #: Set by `_main` once the tracker exists, so `main`'s finally can
        #: write one terminal stage on every exit path without a reference of
        #: its own into `_main`'s locals (C13 fix round, item 6).
        self.tracker: Optional[Progress] = None

    @property
    def stage(self) -> str:
        return self._stage

    @stage.setter
    def stage(self, value: str) -> None:
        self._stage = value
        self.on_change(value)


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
    return bounded.http_client()


def terminate(env: Mapping[str, str], reason: dict) -> None:
    """Write the structured failure reason to the termination log. Exactly one
    call per run: the exit paths below are mutually exclusive, and SIGTERM --
    now the only asynchronous one -- is raised in the main thread."""
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


#: Consecutive pipeline rebuild failures after which the run is abandoned
#: (W9), the same bargain as stream.MAX_UPLOAD_FAILURES: a rebuild that cannot
#: succeed makes every remaining page fail, and because the pages before the
#: first death came out `ok` the all-failed guard never fires -- so the volume
#: would publish a manifest of 600 failures and leave the index green.
MAX_REBUILD_FAILURES = 3


def _default_factory(cfg: Config):
    from . import driver  # htrflow imports stay function-local

    out_dir = Path(cfg.workdir) / "outputs"
    pipeline = driver.load_pipeline(cfg.pipeline_path, out_dir)
    rebuild_failures = 0

    def process(image_path: Path):
        nonlocal pipeline, rebuild_failures
        if pipeline is None:
            # B88: the previous page killed an htrflow worker thread, so that
            # pipeline is unusable. Rebuild here rather than in the handler
            # below, so the failed page keeps its own error -- the models come
            # back from the cache PVC, not the Hub.
            log.warning("rebuilding the htrflow pipeline after a dead worker thread")
            try:
                pipeline = driver.load_pipeline(cfg.pipeline_path, out_dir)
            except Exception as e:
                rebuild_failures += 1
                if rebuild_failures >= MAX_REBUILD_FAILURES:
                    raise Unrecoverable(
                        f"{rebuild_failures} consecutive pipeline rebuilds failed, "
                        f"last: {e!r} — every remaining page would fail the same way"
                    ) from e
                raise
            rebuild_failures = 0
        try:
            files = driver.process_page(pipeline, image_path, out_dir)
        except driver.PipelineDead:
            # Every later page would wait on the dead queue, so this pipeline
            # goes -- weights first: the thread parked in its run() keeps the
            # steps alive, and the rebuild loads a second set onto the same GPU.
            dead, pipeline = pipeline, None
            driver.release_pipeline(dead)
            raise
        provenance.stamp_alto(
            files["alto"],
            image=cfg.image_digest,
            base_revision=cfg.htrflow_base_revision,
        )
        return files

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
        if state.tracker is not None:
            # W4: everything status-shaped is dropped from here on, so the
            # 120 s grace goes to the final log ship instead of three PUTs.
            state.tracker.terminating = True
        raise Terminated()

    previous = _set_signal(signal.SIGTERM, on_sigterm)
    #: The code this run is exiting with, or None while it is still running or
    #: unwinding an exception _main did not classify -- such an exception must
    #: keep its traceback rather than be turned into a code here.
    code: Optional[int] = None
    try:
        code = _main(env, process_page_factory, capture, state)
        return code
    except Terminated:
        # O2: the pod's activeDeadlineSeconds or a node drain. Leave the evidence a
        # failure would (termination message + complete run log), then exit
        # 143 so Kubernetes retries the index like exit 1, not FailIndex.
        # Same shape as the other failure lines: the run viewer's terminal-line
        # regex (frontend runlog.ts) is the contract that stops live polling.
        advice = _advice(False, "SIGTERM")
        log.error("transient failure in %s: SIGTERM — %s", state.stage, advice)
        terminate(env, {"stage": state.stage, "permanent": False, "error": "SIGTERM"})
        code = EXIT_SIGTERM
        return code  # reached only when _hard_exit is stubbed (tests)
    finally:
        # W16: the cleanup is uninterruptible. A drain sends SIGTERM and the
        # node may send a second one; with the handler still installed that
        # one raised Terminated inside this very block -- a traceback out of
        # main, exit 1, and the streams never put back -- and with it already
        # restored it killed the pod outright, losing the log the first had
        # gone to the trouble of preserving.
        _set_signal(signal.SIGTERM, signal.SIG_IGN)
        # C13 fix round, item 6: on every exit path but the successful one
        # (state.stage == "done", already written by _main just before it
        # returns) the file was staying at whatever stage the run was doing
        # when it stopped -- "stream" forever, on a volume that had in fact
        # failed or been SIGTERMed. One terminal write here, from main's own
        # finally rather than from on_sigterm itself (that handler only
        # raises, so Terminated still unwinds through whatever the interrupted
        # frame was doing), covers done/failed/SIGTERM alike.
        if state.tracker is not None and state.stage != "done":
            state.tracker.stage_changed("failed")
        capture.finish()
        if previous is not None:
            _set_signal(signal.SIGTERM, previous)  # W16: after the ship
        if code not in (None, EXIT_OK):
            # W7: every failure exit, not only SIGTERM. Returning normally
            # hands the interpreter a ThreadPoolExecutor to join at shutdown,
            # so a download sitting in its 120 s timeout kept the container
            # alive long after the run had decided to fail. The evidence is
            # already out: the termination message is written and the run log
            # has just been shipped by capture.finish() above.
            _hard_exit(code)


def _main(
    env: Mapping[str, str],
    process_page_factory: Optional[Callable],
    capture: LogCapture,
    state: RunState,
) -> int:
    """config->setup->resume->stream->verify->publish. The wall-clock budget
    is the pod's activeDeadlineSeconds (the converter renders it): at the
    deadline the kubelet SIGTERMs us and main()'s handler does the rest."""
    capture.attach_logging()  # not basicConfig: see LogCapture.attach_logging
    # httpx logs every request, whole URL and all, at INFO: a line a page in
    # the world-readable run log, one redaction miss from a token (W-3).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    t_start = time.monotonic()
    # W10: set on every failure path so queued downloads stop short instead
    # of holding the interpreter (executor workers are joined at exit).
    stop = threading.Event()
    try:
        # `config` is a stage of its own, not the first half of `setup`: a bad
        # or missing env is a deployment fault (converter.yaml, the chart
        # values), and a reader told to go and fix a manifest URL because of
        # one would be sent to the wrong file entirely. The read API and the
        # campaign page key on this field, so the distinction has to be here.
        state.stage = "config"
        cfg = Config.from_env(env)
        state.stage = "setup"
        store = ResultStore(cfg)
        capture.start_shipping(store.put_run_log, cfg.log_ship_seconds)
        # From here on every stage change and every page outcome is published
        # to progress.json, so "where is this volume" is answerable from the
        # bucket while the pod is still running (C13).
        tracker = Progress(cfg, store, capture)
        state.on_change = tracker.stage_changed
        state.tracker = tracker
        # W15: context-managed, so the connection pool and its sockets go when
        # the last stage that fetches anything is done, on every path out. The
        # publish stage below talks only to S3.
        with _http_client() as client:
            source, source_url, pages = _setup(cfg, client, store, state)
            tracker.pages, tracker.source = pages, source
            todo, done = _resume(cfg, store, pages, state)
            stats, nbytes = _stream(
                cfg,
                client,
                store,
                todo,
                done,
                process_page_factory,
                state,
                stop,
                tracker,
            )
        uploaded = _verify(store, pages, stats, state, cfg.last_attempt)
        state.stage = "publish"
        wrote_iiif = publish.run(
            cfg, store, source, source_url, pages, stats, uploaded, t_start, nbytes
        )
        if wrote_iiif:
            tracker.viewer_published = True
        # After manifest.json, which stays the last thing written and the sole
        # completion marker: this only tells a reader the run is over.
        state.stage = "done"

        # No workdir cleanup: it is a memory-backed emptyDir that dies with the
        # pod either way, and a terminated container's tmpfs cannot be
        # inspected -- so deleting it bought nothing and kept nothing.
        return EXIT_OK
    except OSError as e:
        # An I/O condition is never a config mistake, even when it is also a
        # ValueError: huggingface_hub's LocalEntryNotFoundError is a
        # FileNotFoundError on both hub lines and a ValueError on 0.x only,
        # and under HF_HUB_OFFLINE=1 it is what a model missing from the
        # read-only cache raises. Catching OSError first classifies it the
        # same way on either line (this is also driver.build_pipeline's "an
        # OSError from model construction stays transient" contract).
        return _transient(env, state, stop, e)
    except (ConfigError, ManifestError, SetupError, ValueError) as e:
        stop.set()
        stage = state.stage
        log.error("permanent failure in %s: %s — %s", stage, e, _advice(True, str(e)))
        terminate(env, {"stage": stage, "permanent": True, "error": str(e)})
        return EXIT_PERMANENT
    except Exception as e:
        return _transient(env, state, stop, e)


def _advice(permanent: bool, error: str) -> str:
    """The plain-language half of a failure line: what it means for this
    volume and what the operator does next. Both machine-readable halves --
    the prefix the run viewer's terminal-line rule keys on (frontend
    runlog.ts) and the termination JSON -- are untouched."""
    if error.startswith("verify failed: all "):
        return "no page produced a result — check the model and the GPU"
    if error.startswith("verify failed"):
        return "some pages produced no result; the retry redoes only those"
    if error.startswith("SIGTERM"):
        return "stopped by the cluster (drain, pause, or time budget); retried"
    if permanent:
        return "a retry changes nothing — fix the campaign or pipeline file"
    return "the index is retried, resuming from the pages already done"


def _transient(
    env: Mapping[str, str], state: RunState, stop: threading.Event, e: BaseException
) -> int:
    """Retryable: stop the queued downloads (W10), leave the evidence, exit 1."""
    stop.set()
    advice, trace = _advice(False, str(e)), traceback.format_exc()
    log.error("transient failure in %s: %s — %s\n%s", state.stage, e, advice, trace)
    terminate(env, {"stage": state.stage, "permanent": False, "error": str(e)})
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
            cfg.manifest_url,
            client,
            max_bytes=cfg.manifest_max_bytes,
            deadline=cfg.download_deadline_seconds,
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
    A page is done only when both formats are in S3 and were made from the
    source image the page has now (W7, 3096)."""
    state.stage = "resume"
    names = {p.name for p in pages}  # S3 can hold pages this run does not cover
    stored = store.stored_pages()
    done = set.intersection(*stored.values()) & names if cfg.resume else set()
    changed = _changed_sources(store, pages, done) if done else set()
    if changed:
        log.info(
            "[%s] resume: %d done pages have a new source image, reprocessing",
            cfg.volume_ref,
            len(changed),
        )
        done -= changed
    todo = [p for p in pages if p.name not in done]
    # W3, 3096: every page about to be redone loses what it has stored, and
    # before the run, not after -- a changed source, RESUME=false, or half a
    # pair from an attempt that died between its two PUTs. A reprocessing
    # that then fails must leave the page with no outputs at all, else the
    # stale pair answers for it at verify, in the viewer manifest, and as
    # "done" to the next attempt.
    stale = set().union(*stored.values()) & {p.name for p in todo}
    if stale:
        # W-5 (audit 0923): the previous run's completion marker describes
        # the pages about to go, so it goes first -- then the viewer manifest
        # that points at their ALTO, so a reader never meets a manifest.json
        # without its iiif.json. Publish writes both again at the end.
        store.delete("manifest.json")
        store.delete("iiif.json")
        store.delete_pages(stale)
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
    tracker: Progress,
) -> tuple[StreamStats, int]:
    """Download ∥ process ∥ upload, never more than LOOKAHEAD_PAGES ahead:
    the per-page outcomes and the bytes fetched."""
    state.stage = "stream"
    # Seeded with the pages resume skipped BEFORE the loop, not patched up
    # after it: they are in the bucket, so every reader of these counts --
    # progress.json included -- should see them from the first page on.
    stats = StreamStats(results={name: PageOutcome(status="skipped") for name in done})
    tracker.stats = stats
    stream = PageStream(
        todo,
        Path(cfg.workdir) / "input",
        client,
        lookahead=cfg.lookahead_pages,
        concurrency=cfg.download_concurrency,
        max_bytes=cfg.fetch_max_bytes,
        max_pixels=cfg.max_image_pixels,
        stop=stop,
        deadline=cfg.download_deadline_seconds,
    )
    try:
        # The first downloads are in flight, so the model load overlaps them
        # (docs: wrapper, "Model handling") instead of preceding them.
        state.stage = "load"  # W9: a model-load failure is not a stream failure
        process = (factory or _default_factory)(cfg)

        state.stage = "stream"
        # 3096: each page's outputs carry the source they were made from, so a
        # later attempt can tell them from a page made from an older one.
        sources = {p.name: source_digest(p.image_url) for p in todo}
        consume(
            stream,
            process,
            lambda name, files: store.upload_page(name, files, sources[name]),
            stats=stats,
            on_page=tracker.after_page,
        )
    finally:
        stream.close()  # never blocks; cancels what is still queued
    return stats, stream.bytes_fetched


def _verify(
    store: ResultStore,
    pages: list[PageRef],
    stats: StreamStats,
    state: RunState,
    last_attempt: bool = False,
) -> set[str]:
    """D8: every page is accounted for — in S3 with its PAGE and ALTO, skipped
    by resume, or recorded as failed with a reason (the product owner,
    2026-09-14). A page that is *neither* is missing: an inconsistency (an
    upload that never landed), transient, since Kubernetes retries the index
    and resume converges. Failed pages do not fail the volume — a page that
    fails deterministically would fail identically on all four retries and
    leave the bucket without manifest.json — except when every page this run
    processed failed and nothing was resumed, which is a broken model or a
    dead GPU, not a volume.
    Returns what is stored MINUS this run's failed pages (W3): with RESUME
    off there is no `changed` set for `_resume` to delete from, so a page
    reprocessed and failed still has the previous run's objects -- and publish
    would read that ALTO back into iiif.json for a page manifest.json records
    as failed.

    On the index's last attempt a deferred page is failed instead (audit
    0923 W-4): "not now" that lasted every attempt is, for this volume, a
    page that did not come out -- a corrupt file an image server answers 500
    for, a soft-404 served with a 200 -- and missing it would fail the index
    and leave every other page without its completion marker."""
    state.stage = "verify"
    if last_attempt:
        for name, r in list(stats.results.items()):
            if r.status == "deferred":
                why = f"{r.error} (still failing on the index's last attempt)"
                stats.results[name] = PageOutcome(status="failed", error=why)
    uploaded = store.uploaded_pages()
    failed = sorted(n for n, r in stats.results.items() if r.status == "failed")
    # 3095: a page the source could not serve today is missing, whatever a
    # previous run left in the bucket for it (RESUME off keeps those objects).
    deferred = {n for n, r in stats.results.items() if r.status == "deferred"}
    missing = sorted(({p.name for p in pages} - uploaded - set(failed)) | deferred)
    if failed:
        # The run log is now the only place this sentence appears while the
        # run still succeeds; manifest.json keeps the per-page copy.
        log.warning(
            "%d page(s) recorded as failed:%s failed=%s",
            len(failed),
            _failure_detail(stats, failed),
            failed,
        )
    if missing:
        # Counts and the cause FIRST, the name lists last: terminate clips
        # the error field at 3500 chars and a few hundred missing page names
        # fill that on their own — what gets dropped must be the names, never
        # the reason the operator is reading the message for.
        raise RuntimeError(
            f"verify failed: {len(missing)} missing, {len(failed)} failed"
            f"{_failure_detail(stats, failed)}"
            f" missing={missing} failed={failed}"
        )
    statuses = [r.status for r in stats.results.values()]
    # "skipped" not in statuses too: a resume is not a broken model. A volume
    # SIGTERMed at page 637 of 638 whose one remaining page is the dead one
    # would otherwise exit 1, retry, and end FailIndex -- the outcome this
    # whole rule exists to remove.
    if failed and "ok" not in statuses and "skipped" not in statuses:
        raise RuntimeError(
            f"verify failed: all {len(statuses)} processed pages failed"
            f"{_failure_detail(stats, failed)} failed={failed}"
        )
    return uploaded - set(failed)


#: How much of the failed pages' errors goes in the verify message: enough to
#: name the cause, little enough to stay inside terminate's 3500-char field
#: cap (and the 4 KiB the kubelet keeps of a termination message).
FAILED_DETAIL_PAGES = 10
FAILED_DETAIL_CHARS = 200


def _failure_detail(stats: StreamStats, failed: list[str]) -> str:
    """Why those pages failed, for the run log's WARNING line: the operator
    reading the log live should not have to wait for manifest.json (which
    carries the same reasons once the volume completes) to learn the cause."""
    if not failed:
        return ""
    shown = "; ".join(
        f"{n}: {(stats.results[n].error or '')[:FAILED_DETAIL_CHARS]}"
        for n in failed[:FAILED_DETAIL_PAGES]
    )
    rest = len(failed) - FAILED_DETAIL_PAGES
    return f" errors: {shown}" + (f" (+{rest} more)" if rest > 0 else "")


def _synthetic_source(cfg: Config, store: ResultStore) -> tuple[dict, str]:
    """IMAGES: build and publish the synthetic P3 manifest to
    sources/<pipeline>/<volume>/manifest.json (S3_PREFIX honoured, docs:
    wrapper), then hand it back as if it had been fetched."""
    urls = cfg.image_urls
    for u in urls:
        check_http_url(u, "IMAGES URL")
    key = f"sources/{cfg.pipeline_id}/{cfg.volume_ref}/manifest.json"
    manifest_id = f"{cfg.public_results_base.rstrip('/')}/{cfg.root_key(key)}"
    doc = build_manifest(cfg.volume_ref, urls, manifest_id)
    store.put_json_at(key, doc)
    return doc, manifest_id


def _changed_sources(store: ResultStore, pages, done: set[str]) -> set[str]:
    """Done pages made from another source image than the one they have now.

    The page's own record comes first (3096): the digest its outputs were
    stamped with at upload. manifest.json is written only by a COMPLETED run,
    so comparing against it alone meant that after a source change every
    attempt deleted the pages the attempt before had redone from the new
    source -- a volume that needed two attempts never completed.

    A page an older wrapper stored carries no digest, and falls back to what
    the previous completed run recorded in manifest.json (W7). No previous
    manifest, or one with neither field below, means nothing to compare:
    keep them done.

    ``page_source_digests`` is the comparison (W5): the published
    ``page_sources`` are REDACTED (S6, the bucket is public), and a redacted
    URL has lost its query -- so on a host that selects the image with
    ``?id=`` every page looked unchanged forever. The redacted form is still
    read from a manifest written before the digests existed."""
    recorded = store.page_sources(done)
    changed = _differs(pages, done, recorded, source_digest)
    unstamped = {name for name, digest in recorded.items() if digest is None}
    if not unstamped:
        return changed
    previous = store.get_json_or_none("manifest.json") or {}
    digests = previous.get("page_source_digests")
    if isinstance(digests, dict):
        return changed | _differs(pages, unstamped, digests, source_digest)
    sources = previous.get("page_sources")
    if isinstance(sources, dict):
        return changed | _differs(pages, unstamped, sources, redact_url)
    return changed


def _differs(
    pages, done: set[str], stored: dict, form: Callable[[str], str]
) -> set[str]:
    """The done pages recorded under a different identity (a page with no
    record, or a None one, is not compared)."""
    return {
        p.name
        for p in pages
        if p.name in done
        and stored.get(p.name) is not None
        and stored[p.name] != form(p.image_url)
    }
