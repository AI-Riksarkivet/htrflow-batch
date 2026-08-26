"""One reconcile pass (docs: how-it-works/campaigns). Pure orchestration:
adapters injected, no I/O of its own beyond them."""

from __future__ import annotations

import logging
import posixpath
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import s3 as keys
from .attempts import Attempt, dump_attempts, load_attempts
from .guards import check_drift
from .jobspec import (
    ReconcilerConfig,
    build_job,
    build_warmup_job,
    label_value,
    warmup_job_name,
)
from .models import Campaign, PipelineSpec, Volume
from .parse import PipelineError, parse_campaign, parse_pipeline, step_summaries
from .plan import plan_submissions
from .status import JobState, derive, is_permanent, job_name
from .synthetic import build_manifest, classify_manifest

log = logging.getLogger(__name__)

#: Verdicts that keep a volume out of the submission lane (spec §4.4).
_BLOCKING_VERDICTS = ("unreachable", "unsupported")
#: Statuses that get a run_log/run_manifest link: the pod shipped (or ships)
#: its log. Failed volumes surface the same evidence as failure_log instead.
_LOGGED_STATUSES = frozenset({"done", "running", "queued"})

#: Concurrent manifest fetches per validation batch.
_FETCH_WORKERS = 8

#: Errors from reading a file off the checkout: a corrupt or non-UTF-8 file is
#: one campaign's problem, never the whole tick's. Campaign YAML is decoded as
#: UTF-8 explicitly so the verdict does not depend on the CronJob's locale.
_READ_ERRORS = (OSError, UnicodeDecodeError)


def _attempt_key(pipeline_id: str, volume_id: str) -> str:
    """Retry budgets are per (pipeline, volume).

    Re-running a volume under a NEW pipeline id is the upgrade path, and it must
    start from a fresh budget — a volume that burned its attempts on demo-v1
    would otherwise be born ``needs-attention`` on demo-v2.
    """
    return f"{pipeline_id}/{volume_id}"


def _load_repo(campaigns_dir: Path):
    campaigns: list[Campaign] = []
    for p in sorted((campaigns_dir / "campaigns").glob("*.yaml")):
        try:
            text = p.read_text(encoding="utf-8")
        except _READ_ERRORS as e:
            campaigns.append(Campaign(name=p.stem, pipeline_id="", error=str(e)))
            continue
        campaigns.append(parse_campaign(p.stem, text))
    pipelines: dict[str, PipelineSpec] = {}
    errors: list[str] = []
    for p in sorted((campaigns_dir / "pipelines").glob("*.yaml")):
        try:
            text = p.read_text(encoding="utf-8")
        except _READ_ERRORS as e:
            errors.append(f"pipeline {p.stem}: unreadable ({e})")
            continue
        try:
            pipelines[p.stem] = parse_pipeline(p.stem, text)
        except PipelineError as e:
            errors.append(str(e))
    return campaigns, pipelines, errors


def _endpoint(base: str) -> str:
    """``<endpoint>/<bucket>`` -> ``<endpoint>/`` (trailing slash kept so the
    prefix test cannot match a longer hostname)."""
    u = urlsplit(base.rstrip("/"))
    return (
        urlunsplit((u.scheme, u.netloc, posixpath.dirname(u.path), "", "")).rstrip("/")
        + "/"
    )


def _browser_url(url: str | None, cfg: ReconcilerConfig) -> str | None:
    """Map a URL on the in-cluster S3 endpoint to its browser-facing twin.

    status.json is read by browsers, which may not resolve the in-cluster
    endpoint at all (localhost through an SSH forward, a NodePort). The two
    endpoints are the parents of the two results bases (``<endpoint>/<bucket>``),
    so the mapping covers every bucket on that endpoint — fixtures included —
    not just results. Anything hosted elsewhere passes through untouched.
    """
    if url is None or not cfg.internal_results_base:
        return url
    internal = _endpoint(cfg.internal_results_base)
    if not url.startswith(internal):
        return url
    return _endpoint(cfg.public_results_base) + url[len(internal) :]


def _source_manifest_url(
    volume: Volume, pipeline_id: str, bucket, cfg: ReconcilerConfig, cached: dict
) -> tuple[str, str]:
    """(browser URL, job URL) for the volume's source manifest.

    The synthetic manifest is written once under its PUBLIC id (browsers
    open it from the status page), but the Job fetches it via the
    in-cluster S3 endpoint — public_results_base may be browser-only
    (e.g. localhost through an SSH forward), and localhost inside a pod is
    the pod itself. ``cached`` is the volume's entry in status/volumes.json:
    the key written last time is recorded there, so a steady-state tick
    costs no GET per images: volume (audit X1).
    """
    if volume.manifest_url:
        # _browser_url is total for a non-None input; `or` keeps the return
        # type honest for the type checker.
        public = _browser_url(volume.manifest_url, cfg) or volume.manifest_url
        return public, volume.manifest_url
    key = keys.synthetic_manifest_key(pipeline_id, volume.id, volume.images)
    public = f"{cfg.public_results_base.rstrip('/')}/{key}"
    if cached.get("synthetic") != key:
        bucket.write_json(key, build_manifest(volume.id, list(volume.images), public))
        cached["synthetic"] = key
    base = cfg.internal_results_base or cfg.public_results_base
    return public, f"{base.rstrip('/')}/{key}"


def _preserve_failure_log(
    bucket, cluster, pid: str, vid: str, *, retire_run_log: bool
) -> None:
    """Failure evidence to status/failures/: the wrapper's shipped run log when
    there is one (complete), else the kube-API tail. On the retry path the
    run-log key is retired too, so the next attempt is never linked to the
    previous attempt's log as if it were live."""
    run_key = keys.run_log_key(pid, vid)
    shipped = bucket.read_text(run_key)
    bucket.put_text(
        keys.failure_log_key(pid, vid),
        shipped if shipped is not None else cluster.job_logs(job_name(pid, vid)),
    )
    if retire_run_log and shipped is not None:
        bucket.delete(run_key)


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _first_canvas(doc: object) -> dict:
    if not isinstance(doc, dict):
        return {}
    canvases = _as_list(doc.get("items"))
    if not canvases:
        seqs = _as_list(doc.get("sequences"))
        first_seq = seqs[0] if seqs else None
        if isinstance(first_seq, dict):
            canvases = _as_list(first_seq.get("canvases"))
    first = canvases[0] if canvases else {}
    return first if isinstance(first, dict) else {}


def _services(node: dict) -> list:
    svc = node.get("service")
    if isinstance(svc, dict):
        return [svc]
    return _as_list(svc)


def _sized(service_id: str) -> str:
    return f"{service_id.rstrip('/')}/full/200,/0/default.jpg"


def _thumbnail(doc: object) -> str | None:
    """First-page thumbnail: sized request when a service exists, else the
    direct image URL (spec §5). Handles P3 and P2 canvases.

    Total over junk: a manifest we could fetch but not understand yields no
    thumbnail rather than crashing the whole tick.
    """
    try:
        canvas = _first_canvas(doc)
        for ap in _as_list(canvas.get("items")):
            for anno in _as_list(ap.get("items")):
                body = anno.get("body") or {}
                for svc in _services(body):
                    sid = svc.get("id") or svc.get("@id")
                    if sid:
                        return _sized(str(sid))
                if body.get("id"):
                    return str(body["id"])
        for img in _as_list(canvas.get("images")):
            res = img.get("resource") or {}
            for svc in _services(res):
                sid = svc.get("@id") or svc.get("id")
                if sid:
                    return _sized(str(sid))
            direct = res.get("@id") or res.get("id")
            if direct:
                return str(direct)
    except (AttributeError, TypeError, IndexError, KeyError):
        return None
    return None


def _page_count(doc: object) -> int | None:
    """Canvas count for P3 (items) and P2 (sequences[0].canvases) manifests."""
    if not isinstance(doc, dict):
        return None
    canvases = _as_list(doc.get("items"))
    if not canvases:
        seqs = _as_list(doc.get("sequences"))
        first_seq = seqs[0] if seqs else None
        if isinstance(first_seq, dict):
            canvases = _as_list(first_seq.get("canvases"))
    return len(canvases) or None


def _classify(doc: object) -> str:
    """``classify_manifest`` hardened for the open web.

    A P3 Collection carries ``items`` just like a Manifest does, so the bare
    classifier would call it ``p3`` and the reconciler would burn a job on a
    document the wrapper cannot read. Anything else unparseable is
    ``unsupported`` rather than an exception that would abort the tick.
    """
    if not isinstance(doc, dict):
        return "unsupported"
    if doc.get("type") == "Collection":
        return "unsupported"
    try:
        return classify_manifest(doc)
    except (AttributeError, TypeError, IndexError, KeyError):
        return "unsupported"


def _verdict(doc: object) -> dict:
    """Verdict per fetched manifest (spec §4.4): format + thumbnail + pages."""
    if doc is None:
        return {"format": "unreachable", "thumbnail": None, "page_count": None}
    return {
        "format": _classify(doc),
        "thumbnail": _thumbnail(doc),
        "page_count": _page_count(doc),
    }


def _parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_verdict(cache: dict, url: str, now_iso: str) -> dict | None:
    """The cached verdict for ``url`` if it still stands.

    A verdict about the DOCUMENT is cached forever — a Collection or a P2-less
    manifest will not become submittable by being asked again. ``unreachable``
    is a verdict about the NETWORK: it is cached for a few ticks
    (``unreachable_until``) so a dead host costs a timeout once, not every
    tick, but never forever — a flaky fetch must not wedge a volume out of
    its campaign permanently.
    """
    v = cache.get(url)
    if not isinstance(v, dict):
        return None
    if v.get("format") == "unreachable" and v.get("unreachable_until", "") <= now_iso:
        return None
    return v


def _describe(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def _public(cfg: ReconcilerConfig, key: str) -> str:
    return f"{cfg.public_results_base.rstrip('/')}/{key}"


class _Pass:
    """State of one tick. Every S3/kube effect is contained per volume (a bad
    response marks that row ``error`` and the tick goes on) and the retry
    budget is persisted the moment it changes (audit R3)."""

    def __init__(
        self,
        campaigns_dir: Path,
        bucket,
        cluster,
        cfg: ReconcilerConfig,
        now_iso: str,
        fetch_json,
    ) -> None:
        self.bucket = bucket
        self._calls_before = getattr(bucket, "calls", 0)
        self.cluster = cluster
        self.cfg = cfg
        self.now_iso = now_iso
        self.fetch_json = fetch_json
        self.campaigns, self.pipelines, self.warnings = _load_repo(Path(campaigns_dir))
        self.jobs: dict[str, JobState] = cluster.jobs()
        self.attempts = load_attempts(self._owned_json(keys.attempts_key()))
        self.validation = self._owned_json(keys.validation_key())
        self.volumes = self._owned_json(keys.volumes_key())
        self.blocked: set[str] = set()
        self.submitted = 0
        self.retried = 0
        self.validations = 0
        # done_volumes is a paginated LIST + a HEAD per volume: probe each
        # pipeline once per tick, not once per campaign that uses it.
        self._done_cache: dict[str, dict[str, str]] = {}

    def _owned_json(self, key: str) -> dict:
        """A reconciler-owned JSON file, or ``{}`` with a warning when it is
        corrupt (audit R8): a truncated upload must not poison every tick
        until an operator notices."""
        try:
            raw = self.bucket.read_json(key)
        except ValueError as e:
            self.warnings.append(f"{key}: unreadable ({e}) — treated as absent")
            log.warning("%s: unreadable (%s); treated as absent", key, e)
            return {}
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            self.warnings.append(f"{key}: not a JSON object — treated as absent")
            return {}
        return raw

    def done_for(self, pipeline_id: str) -> dict[str, str]:
        if pipeline_id not in self._done_cache:
            self._done_cache[pipeline_id] = self.bucket.done_volumes(pipeline_id)
        return self._done_cache[pipeline_id]

    def save_attempts(self) -> None:
        self.bucket.write_json(keys.attempts_key(), dump_attempts(self.attempts))

    def volume_cache(self, pid: str, vid: str) -> dict:
        return self.volumes.setdefault(f"{pid}/{vid}", {})

    # -- pre-validation --------------------------------------------------------

    def validate(self) -> None:
        """Fetch a bounded batch of not-yet-validated manifests concurrently
        and persist the verdicts at once (audit X1): validation is O(new
        volumes), never O(volumes), and a deadline-killed tick keeps what it
        already paid for. Volumes past the bound wait for a later tick."""
        if self.fetch_json is None:
            return
        todo: list[str] = []
        seen: set[str] = set()
        for camp in self.campaigns:
            if camp.error or camp.pipeline_id not in self.pipelines:
                continue
            done = self.done_for(camp.pipeline_id)
            for v in camp.volumes:
                url = v.manifest_url
                if not url or url in seen or v.id in done:
                    continue
                if _valid_verdict(self.validation, url, self.now_iso) is not None:
                    continue
                seen.add(url)
                todo.append(url)
        batch = todo[: self.cfg.max_validations_per_tick]
        if not batch:
            return
        until = _iso(
            _parse_iso(self.now_iso)
            + timedelta(seconds=self.cfg.unreachable_ticks * self.cfg.tick_seconds)
        )
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            docs = list(pool.map(self._fetch, batch))
        for url, doc in zip(batch, docs):
            verdict = _verdict(doc)
            if verdict["format"] == "unreachable":
                verdict["unreachable_until"] = until
            self.validation[url] = verdict
        self.validations = len(batch)
        self.bucket.write_json(keys.validation_key(), self.validation)

    def _fetch(self, url: str) -> object:
        try:
            return self.fetch_json(url)
        except Exception as e:  # noqa: BLE001 — a bad fetch is "unreachable"
            log.warning("fetch %s: %s", url, _describe(e))
            return None

    # -- pipelines -----------------------------------------------------------

    def check_pipelines(self) -> None:
        for pid, spec in self.pipelines.items():
            try:
                self._check_pipeline(pid, spec)
            except Exception as e:  # noqa: BLE001 — one pipeline, not the tick
                self.warnings.append(f"pipeline {pid}: {_describe(e)}")
                self.blocked.add(pid)

    def _check_pipeline(self, pid: str, spec: PipelineSpec) -> None:
        published = None
        done_probe = self.done_for(pid)
        if done_probe:
            published = self.bucket.read_json(
                keys.manifest_key(pid, sorted(done_probe)[0])
            )
        # Drift is checked BEFORE the ConfigMap is applied: applying first would
        # overwrite the very evidence the check reads, silently laundering a
        # recipe change into a pipeline id that already has published results.
        ok, msg = check_drift(spec, self.cluster.get_configmap_steps(pid), published)
        if msg:
            self.warnings.append(msg)
        if not ok:
            self.blocked.add(pid)
        else:
            self.cluster.ensure_configmap(pid, spec.steps_yaml)

    def warm_pipelines(self) -> None:
        """Warm-up gate: batch Jobs run offline on a read-only model cache, so
        a pipeline submits nothing until its warm-up Job — the one writer of
        that cache — has completed. A failed warm-up is logged, deleted and
        recreated next tick: HF Hub outages heal themselves, and the retry
        costs nothing. Lazy on purpose: only pipelines with volumes still to
        run are warmed — a finished pipeline has nothing to gate, and one
        pinned to an image that predates the warm-up entrypoint would
        otherwise fail every tick."""
        in_use = {
            c.pipeline_id
            for c in self.campaigns
            if not c.error
            and c.pipeline_id in self.pipelines
            and any(v.id not in self.done_for(c.pipeline_id) for v in c.volumes)
        }
        warmups = self.cluster.warmups()
        for pid, spec in self.pipelines.items():
            if pid in self.blocked or pid not in in_use:
                continue
            state = warmups.get(pid.lower())
            if state is not None and state.succeeded:
                continue
            self.blocked.add(pid)
            name = warmup_job_name(pid)
            try:
                self._warm(pid, spec, state, name)
            except Exception as e:  # noqa: BLE001
                self.warnings.append(f"pipeline {pid}: warm-up: {_describe(e)}")

    def _warm(self, pid: str, spec: PipelineSpec, state: JobState | None, name: str):
        """Warm-ups have the same budget as volumes (audit R7): a permanent
        failure (exit 13: bad model id, bad YAML) or the attempt cap parks the
        pipeline with its evidence kept, instead of a delete-recreate loop
        every tick forever. Clearing ``warmup/<pid>`` in attempts.json (or a
        new pipeline id) is the operator's retry."""
        akey = f"warmup/{pid}"
        record = self.attempts.get(akey, Attempt())
        log_key = keys.warmup_log_key(pid)
        if record.terminal:
            self.warnings.append(
                f"pipeline {pid}: model warm-up needs attention ({record.terminal}); "
                f"log at {log_key}; clear {akey} in attempts.json to retry"
            )
            return
        if state is None:
            self.cluster.create_job(build_warmup_job(spec, self.cfg))
            self.warnings.append(f"pipeline {pid}: warming model cache ({name})")
        elif state.failed:
            self.bucket.put_text(log_key, self.cluster.job_logs(name))
            n = record.n + 1
            verdict = None
            if is_permanent(state):
                verdict = "exit-13"
            elif n >= self.cfg.attempt_cap:
                verdict = "capped"
            self.attempts[akey] = Attempt(n=n, terminal=verdict)
            self.save_attempts()
            if verdict:
                self.warnings.append(
                    f"pipeline {pid}: model warm-up needs attention ({verdict}, "
                    f"exit {state.exit_code}); log at {log_key}; clear {akey} in "
                    "attempts.json to retry"
                )
                return
            self.cluster.delete_job(name)
            self.warnings.append(
                f"pipeline {pid}: model warm-up failed (exit {state.exit_code}, "
                f"attempt {n}/{self.cfg.attempt_cap}); log at {log_key}, "
                "retrying next tick"
            )
        else:
            self.warnings.append(f"pipeline {pid}: warming model cache ({name})")

    # -- campaigns -----------------------------------------------------------

    def campaign_entry(self, camp: Campaign) -> dict[str, Any]:
        spec = self.pipelines.get(camp.pipeline_id)
        return {
            "name": camp.name,
            "pipeline": camp.pipeline_id or None,
            "pipeline_steps": step_summaries(spec.steps_yaml) if spec else None,
            "pipeline_yaml": spec.steps_yaml if spec else None,
            "error": camp.error,
            "totals": {
                "done": 0,
                "total": len(camp.volumes),
                "pages_done": None,
                "pages_total": None,
            },
            "volumes": [],
            "orphans": [],
        }

    def volume_row(
        self,
        pid: str,
        v: Volume,
        done: dict[str, str],
        budgets: dict[str, Attempt],
    ) -> tuple[dict[str, Any], str | None]:
        """(status row, job manifest URL if the volume should be submitted).

        The row is built incrementally so a failing effect leaves the fields
        already known in place and only marks the row ``error``.
        """
        cfg, bucket, cluster = self.cfg, self.bucket, self.cluster
        st = derive(v, pid, done, self.jobs, budgets, cfg.attempt_cap)
        akey = _attempt_key(pid, v.id)
        record = self.attempts.get(akey, Attempt())
        row: dict[str, Any] = {
            "id": v.id,
            "status": st,
            "attempts": record.n,
            "terminal": record.terminal,
            "updated": done.get(v.id),
            "failure_log": None,
            "run_log": None,
            "pages_done": None,
            "pages_total": len(v.images) if v.images else None,
            "error": None,
            "viewer_manifest": None,
            "run_manifest": None,
            "source_manifest": None,
            "thumbnail": None,
        }
        submit: str | None = None
        vcache = self.volume_cache(pid, v.id)
        try:
            public_src, job_src = _source_manifest_url(v, pid, bucket, cfg, vcache)
            row["source_manifest"] = public_src
            # The thumbnail is read from the cache unconditionally — a done
            # volume still needs its picture, and a cache hit costs nothing.
            # Only the status override is gated; the fetch happened in
            # ``validate`` (or has not happened yet: then the volume waits).
            cached = self.validation.get(v.manifest_url) if v.manifest_url else None
            thumb = cached.get("thumbnail") if cached else None
            validated = True
            if v.manifest_url and self.fetch_json is not None and st != "done":
                verdict = _valid_verdict(self.validation, v.manifest_url, self.now_iso)
                if verdict is None:
                    validated = False  # past this tick's validation bound
                elif verdict["format"] in _BLOCKING_VERDICTS:
                    st = row["status"] = verdict["format"]  # no job burned
            if not v.manifest_url:
                # Synthetic manifests carry no IIIF service, so the picture is
                # the first image itself — the same fallback _thumbnail
                # applies to service-less external manifests.
                thumb = v.images[0] if v.images else None
            row["thumbnail"] = _browser_url(thumb, cfg)
            if st in ("retry", "needs-attention"):
                row["failure_log"] = _public(cfg, keys.failure_log_key(pid, v.id))
            # Cleanup and the attempt bump are gated on the pipeline too: a
            # drift-blocked pipeline submits nothing, so it must not spend the
            # volume's retry budget while the operator sorts the drift out.
            if st == "retry" and pid not in self.blocked:
                # Bump and PERSIST before the destructive delete (R3): an
                # abort in between must not make the attempt free. The
                # volume re-enters the lane next tick, once the Job is gone —
                # creating the same name in the same tick as a Foreground
                # delete only yields a 409 and a wasted window slot (R2).
                record = record.model_copy(update={"n": record.n + 1})
                self.attempts[akey] = budgets[v.id] = record
                row["attempts"] = record.n
                self.save_attempts()
                self.retried += 1
                _preserve_failure_log(bucket, cluster, pid, v.id, retire_run_log=True)
                cluster.delete_job(job_name(pid, v.id))
            elif st == "pending" and pid not in self.blocked and validated:
                submit = job_src
            elif st == "needs-attention":
                job = self.jobs.get(job_name(pid, v.id))
                if record.terminal is None:
                    # First sighting of the verdict: persist it NOW (R1). The
                    # Job carrying the evidence is TTL-reaped within 24h;
                    # without the record the volume would read pending again
                    # and burn a GPU run every day forever.
                    verdict = (
                        "exit-13" if job is not None and is_permanent(job) else "capped"
                    )
                    record = record.model_copy(update={"terminal": verdict})
                    self.attempts[akey] = budgets[v.id] = record
                    row["terminal"] = verdict
                    self.save_attempts()
                if job is not None:
                    # The retry path uploads logs before deleting the Job; an
                    # exit-13 (or capped) volume otherwise reaches its terminal
                    # state with no uploaded evidence. Idempotent overwrite;
                    # the run-log key stays so later ticks keep copying the
                    # complete log rather than falling back to the kube tail.
                    _preserve_failure_log(
                        bucket, cluster, pid, v.id, retire_run_log=False
                    )
            if st == "done":
                row["viewer_manifest"] = _public(cfg, f"{pid}/{v.id}/iiif.json")
            if st in _LOGGED_STATUSES:
                # The run's manifest.json (summary card in the run viewer);
                # 404s until the wrapper publishes, which the viewer tolerates.
                row["run_manifest"] = _public(cfg, f"{pid}/{v.id}/manifest.json")
            if st == "done":
                self._done_probe(pid, v.id, done[v.id], vcache, row)
            elif st == "running":
                row["pages_done"] = bucket.count_pages(pid, v.id)
            if row["pages_total"] is None:
                cached_v = (
                    self.validation.get(v.manifest_url) if v.manifest_url else None
                )
                row["pages_total"] = cached_v.get("page_count") if cached_v else None
            if row["pages_total"] is None and st == "done":
                row["pages_total"] = row["pages_done"]
            if st != "done":
                row["run_log"] = self._run_log(pid, v.id, st)
        except Exception as e:  # noqa: BLE001 — one volume, never the tick
            row["error"] = _describe(e)
            log.warning("volume %s/%s: %s", pid, v.id, row["error"])
            submit = None
        return row, submit

    def _done_probe(
        self, pid: str, vid: str, updated: str, vcache: dict, row: dict
    ) -> None:
        """Page count and run-log link of a done volume, off status/volumes.json
        when the manifest mtime is unchanged (audit X1): a finished volume is
        immutable under its mtime, so the steady state costs no S3 call.
        A negative run-log probe is cached only once the Job is gone — while
        it lingers (24h TTL) the kube-tail upload may still happen."""
        if vcache.get("updated") != updated:
            vcache.clear()
            vcache["updated"] = updated
        if "pages" not in vcache:
            vcache["pages"] = self.bucket.count_pages(pid, vid)
        row["pages_done"] = vcache["pages"]
        log_key = keys.run_log_key(pid, vid)
        if "run_log" not in vcache:
            link = self._run_log(pid, vid, "done")
            if link is not None:
                vcache["run_log"] = True
            elif job_name(pid, vid) not in self.jobs:
                vcache["run_log"] = False
            row["run_log"] = link
        elif vcache["run_log"]:
            row["run_log"] = _public(self.cfg, log_key)

    def _run_log(self, pid: str, vid: str, st: str) -> str | None:
        """The wrapper ships its own log to this key while it runs, so any
        status a pod could have produced gets the link (live for running
        volumes). The kube-API upload is the fallback for images that predate
        the shipper — done + succeeded Job, no key."""
        if st not in _LOGGED_STATUSES:
            return None
        log_key = keys.run_log_key(pid, vid)
        if self.bucket.exists(log_key):
            return _public(self.cfg, log_key)
        if st == "done":
            job = self.jobs.get(job_name(pid, vid))
            if job is not None and job.succeeded:
                # Jobs linger ttlSecondsAfterFinished (24h) after Complete —
                # one upload per volume, guarded by the HEAD above.
                self.bucket.put_text(
                    log_key, self.cluster.job_logs(job_name(pid, vid), tail=500)
                )
                return _public(self.cfg, log_key)
        return None

    def campaign(
        self, camp: Campaign, entry: dict[str, Any], claimed: dict[str, set[str]]
    ) -> list[tuple[Volume, str]]:
        """Fill ``entry`` for one campaign; returns its submission lane."""
        pid = camp.pipeline_id
        done = self.done_for(pid)
        # ``derive`` reads attempts by volume id; the persisted counter is keyed
        # by pipeline too, so hand derive a view of just this pipeline's budgets
        # and keep the two in step as counters are bumped.
        prefix = f"{pid}/"
        budgets = {
            k[len(prefix) :]: rec
            for k, rec in self.attempts.items()
            if k.startswith(prefix)
        }
        lane: list[tuple[Volume, str]] = []
        for v in camp.volumes:
            row, submit = self.volume_row(pid, v, done, budgets)
            entry["volumes"].append(row)
            if row["status"] == "done":
                entry["totals"]["done"] += 1
            if submit is not None:
                lane.append((v, submit))
        known_totals = [
            v["pages_total"] for v in entry["volumes"] if v["pages_total"] is not None
        ]
        known_done = [
            v["pages_done"] for v in entry["volumes"] if v["pages_done"] is not None
        ]
        entry["totals"]["pages_total"] = sum(known_totals) if known_totals else None
        entry["totals"]["pages_done"] = sum(known_done) if known_done else None
        return lane

    # -- the pass ------------------------------------------------------------

    def run(self) -> dict:
        started = time.monotonic()
        cfg = self.cfg
        self.check_pipelines()
        self.warm_pipelines()
        self.validate()
        doc: dict[str, Any] = {
            "generated_at": self.now_iso,
            "tick_seconds": cfg.tick_seconds,
            "campaigns_repo_url": cfg.campaigns_repo_web_url or cfg.campaigns_repo_url,
            "warnings": self.warnings,
            "tick_summary": {},
            "campaigns": [],
        }
        # Orphans are a property of the PIPELINE prefix, not of one campaign:
        # two campaigns on the same pipeline share the result namespace, so a
        # volume claimed by either is not an orphan. Reported once, on the
        # first campaign using that pipeline.
        claimed: dict[str, set[str]] = {}
        for camp in self.campaigns:
            if camp.pipeline_id not in self.pipelines:
                continue
            # A campaign that failed to parse still names its volumes (R14).
            claimed.setdefault(camp.pipeline_id, set()).update(camp.declared_ids)
        orphans_reported: set[str] = set()
        pending: dict[str, list[tuple[Volume, str]]] = {}
        for camp in self.campaigns:
            entry = self.campaign_entry(camp)
            doc["campaigns"].append(entry)
            if camp.error or camp.pipeline_id not in self.pipelines:
                if not camp.error:
                    entry["error"] = f"unknown pipeline: {camp.pipeline_id}"
                continue
            pid = camp.pipeline_id
            try:
                if pid not in orphans_reported:
                    entry["orphans"] = sorted(self.done_for(pid).keys() - claimed[pid])
                    orphans_reported.add(pid)
                lane = self.campaign(camp, entry, claimed)
            except Exception as e:  # noqa: BLE001 — one campaign, not the tick
                entry["error"] = _describe(e)
                log.warning("campaign %s: %s", camp.name, entry["error"])
                continue
            if pid not in self.blocked and lane:
                pending[camp.name] = lane
        self.submit(pending)
        self.save_attempts()
        self.bucket.write_json(keys.volumes_key(), self.volumes)
        # status.json is the last write, and the three writes below it are
        # counted in: the number the operator sees is the tick's whole cost.
        summary = {
            "seconds": round(time.monotonic() - started, 3),
            "s3_calls": getattr(self.bucket, "calls", 0) - self._calls_before + 1,
            "validations": self.validations,
            "submitted": self.submitted,
            "retried": self.retried,
        }
        doc["tick_summary"] = summary
        self.bucket.write_json(keys.status_key(), doc)
        log.info(
            "tick: seconds=%s s3_calls=%d validations=%d submitted=%d retried=%d "
            "warnings=%d",
            summary["seconds"],
            summary["s3_calls"],
            summary["validations"],
            summary["submitted"],
            summary["retried"],
            len(self.warnings),
        )
        return doc

    def submit(self, pending: dict[str, list[tuple[Volume, str]]]) -> None:
        # Only genuinely pending/running Jobs occupy a window slot (plus
        # Terminating ones, whose pod may still hold the GPU). Terminal Jobs
        # are done with the cluster; counting the succeeded ones leaks the
        # window shut as a campaign completes (their TTL is 24h).
        in_flight = sum(1 for j in self.jobs.values() if j.in_flight)
        # Fairness (audit R5): campaigns with the fewest Jobs in flight go
        # first, so a big campaign cannot keep the free slot tick after tick.
        # Ties fall back to file order (the name).
        per_campaign = Counter(j.campaign for j in self.jobs.values() if j.in_flight)
        order = sorted(pending, key=lambda n: (per_campaign[label_value(n)], n))
        lanes = {name: [v for v, _ in pending[name]] for name in order}
        srcs = {(n, v.id): s for n, lane in pending.items() for v, s in lane}
        for camp_name, volume in plan_submissions(lanes, in_flight, self.cfg.window):
            camp = next(c for c in self.campaigns if c.name == camp_name)
            spec = self.pipelines[camp.pipeline_id]
            job = build_job(
                spec,
                volume,
                srcs[(camp_name, volume.id)],
                self.cfg,
                campaign=camp_name,
            )
            try:
                self.cluster.create_job(job)
            except Exception as e:  # noqa: BLE001 — one submission
                self.warnings.append(
                    f"campaign {camp_name}: submit {volume.id}: {_describe(e)}"
                )
                continue
            self.submitted += 1


def tick(
    campaigns_dir: Path,
    bucket,
    cluster,
    cfg: ReconcilerConfig,
    now_iso: str,
    fetch_json=None,
) -> dict:
    """One reconcile pass under the per-tick Lease (audit O8): a second tick
    started by hand while one is running would double-submit and
    double-charge attempts, so it is skipped with a log line instead."""
    if not cluster.acquire_lease(cfg.lease_name, cfg.tick_deadline_seconds):
        log.warning("tick skipped: lease %s is held by another tick", cfg.lease_name)
        return {"generated_at": now_iso, "skipped": "lease held"}
    try:
        return _Pass(campaigns_dir, bucket, cluster, cfg, now_iso, fetch_json).run()
    finally:
        cluster.release_lease(cfg.lease_name)
