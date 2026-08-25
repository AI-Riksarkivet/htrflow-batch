"""One reconcile pass (docs: how-it-works/campaigns). Pure orchestration:
adapters injected, no I/O of its own beyond them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import s3 as keys
from .guards import check_drift
from .jobspec import ReconcilerConfig, build_job, build_warmup_job, warmup_job_name
from .models import Campaign, PipelineSpec, Volume
from .parse import PipelineError, parse_campaign, parse_pipeline, step_summaries
from .plan import plan_submissions
from .status import derive, job_name
from .synthetic import build_manifest, classify_manifest

TICK_SECONDS = 300

#: Verdicts that keep a volume out of the submission lane (spec §4.4).
_BLOCKING_VERDICTS = ("unreachable", "unsupported")

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


def _source_manifest_url(
    volume: Volume, pipeline_id: str, bucket, cfg: ReconcilerConfig
) -> tuple[str, str]:
    """(browser URL, job URL) for the volume's source manifest.

    The synthetic manifest is written once under its PUBLIC id (browsers
    open it from the status page), but the Job fetches it via the
    in-cluster S3 endpoint — public_results_base may be browser-only
    (e.g. localhost through an SSH forward), and localhost inside a pod is
    the pod itself.
    """
    if volume.manifest_url:
        return volume.manifest_url, volume.manifest_url
    key = keys.synthetic_manifest_key(pipeline_id, volume.id)
    public = f"{cfg.public_results_base.rstrip('/')}/{key}"
    if bucket.read_json(key) is None:
        bucket.write_json(key, build_manifest(volume.id, list(volume.images), public))
    base = cfg.internal_results_base or cfg.public_results_base
    return public, f"{base.rstrip('/')}/{key}"


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


def _validate(url: str, cache: dict, fetch_json) -> dict:
    """Verdict per manifest URL (spec §4.4): format + thumbnail + page_count.

    A verdict about the DOCUMENT is cached forever — a Collection or a P2-less
    manifest will not become submittable by being asked again. ``unreachable``
    is a verdict about the NETWORK and is deliberately never cached: a single
    flaky fetch must not wedge a volume out of its campaign permanently, so it
    is re-probed on the next tick.
    """
    if url in cache:
        return cache[url]
    doc = fetch_json(url)
    if doc is None:
        return {"format": "unreachable", "thumbnail": None, "page_count": None}
    verdict = {
        "format": _classify(doc),
        "thumbnail": _thumbnail(doc),
        "page_count": _page_count(doc),
    }
    cache[url] = verdict
    return verdict


def tick(
    campaigns_dir: Path,
    bucket,
    cluster,
    cfg: ReconcilerConfig,
    now_iso: str,
    fetch_json=None,
) -> dict:
    campaigns, pipelines, warnings = _load_repo(Path(campaigns_dir))
    jobs = cluster.jobs()
    attempts: dict = bucket.read_json(keys.attempts_key()) or {}
    validation: dict = bucket.read_json(keys.validation_key()) or {}
    blocked: set[str] = set()

    # done_volumes is a paginated LIST + a HEAD per volume: probe each pipeline
    # once per tick, not once per campaign that uses it.
    done_cache: dict[str, dict[str, str]] = {}

    def done_for(pipeline_id: str) -> dict[str, str]:
        if pipeline_id not in done_cache:
            done_cache[pipeline_id] = bucket.done_volumes(pipeline_id)
        return done_cache[pipeline_id]

    for pid, spec in pipelines.items():
        published = None
        done_probe = done_for(pid)
        if done_probe:
            published = bucket.read_json(keys.manifest_key(pid, sorted(done_probe)[0]))
        # Drift is checked BEFORE the ConfigMap is applied: applying first would
        # overwrite the very evidence the check reads, silently laundering a
        # recipe change into a pipeline id that already has published results.
        ok, msg = check_drift(spec, cluster.get_configmap_steps(pid), published)
        if msg:
            warnings.append(msg)
        if not ok:
            blocked.add(pid)
        else:
            cluster.ensure_configmap(pid, spec.steps_yaml)

    # Warm-up gate: batch Jobs run offline on a read-only model cache, so a
    # pipeline submits nothing until its warm-up Job — the one writer of that
    # cache — has completed. A failed warm-up is logged, deleted and recreated
    # next tick: HF Hub outages heal themselves, and the retry costs nothing.
    # Lazy on purpose: only pipelines with volumes still to run are warmed —
    # a finished pipeline has nothing to gate, and one pinned to an image
    # that predates the warm-up entrypoint would otherwise fail every tick.
    in_use = {
        c.pipeline_id
        for c in campaigns
        if not c.error
        and c.pipeline_id in pipelines
        and any(v.id not in done_for(c.pipeline_id) for v in c.volumes)
    }
    warmups = cluster.warmups()
    for pid, spec in pipelines.items():
        if pid in blocked or pid not in in_use:
            continue
        state = warmups.get(pid.lower())
        if state is not None and state.succeeded:
            continue
        blocked.add(pid)
        name = warmup_job_name(pid)
        if state is None:
            cluster.create_job(build_warmup_job(spec, cfg))
            warnings.append(f"pipeline {pid}: warming model cache ({name})")
        elif state.failed:
            bucket.put_text(keys.warmup_log_key(pid), cluster.job_logs(name))
            cluster.delete_job(name)
            warnings.append(
                f"pipeline {pid}: model warm-up failed (exit {state.exit_code}); "
                f"log at {keys.warmup_log_key(pid)}, retrying next tick"
            )
        else:
            warnings.append(f"pipeline {pid}: warming model cache ({name})")

    doc: dict[str, Any] = {
        "generated_at": now_iso,
        "tick_seconds": TICK_SECONDS,
        "campaigns_repo_url": cfg.campaigns_repo_web_url or cfg.campaigns_repo_url,
        "warnings": warnings,
        "campaigns": [],
    }

    # Orphans are a property of the PIPELINE prefix, not of one campaign: two
    # campaigns on the same pipeline share the result namespace, so a volume
    # claimed by either is not an orphan. Reported once, on the first campaign
    # using that pipeline.
    claimed: dict[str, set[str]] = {}
    for camp in campaigns:
        if camp.error or camp.pipeline_id not in pipelines:
            continue
        claimed.setdefault(camp.pipeline_id, set()).update(v.id for v in camp.volumes)
    orphans_reported: set[str] = set()

    pending: dict[str, list[tuple[Volume, str]]] = {}
    # Only genuinely pending/running Jobs occupy a window slot. Terminal Jobs —
    # failed OR succeeded — are done with the cluster; counting the succeeded
    # ones leaks the window shut as a campaign completes (their TTL is 24h).
    in_flight = sum(1 for j in jobs.values() if not (j.failed or j.succeeded))

    for camp in campaigns:
        entry: dict[str, Any] = {
            "name": camp.name,
            "pipeline": camp.pipeline_id or None,
            "pipeline_steps": (
                step_summaries(pipelines[camp.pipeline_id].steps_yaml)
                if camp.pipeline_id in pipelines
                else None
            ),
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
        doc["campaigns"].append(entry)
        if camp.error or camp.pipeline_id not in pipelines:
            if not camp.error:
                entry["error"] = f"unknown pipeline: {camp.pipeline_id}"
            continue
        pid = camp.pipeline_id
        done = done_for(pid)
        if pid not in orphans_reported:
            entry["orphans"] = sorted(done.keys() - claimed[pid])
            orphans_reported.add(pid)
        # ``derive`` reads attempts by volume id; the persisted counter is keyed
        # by pipeline too, so hand derive a view of just this pipeline's budgets
        # and keep the two in step as counters are bumped below.
        prefix = f"{pid}/"
        budgets = {
            k[len(prefix) :]: n for k, n in attempts.items() if k.startswith(prefix)
        }
        lane: list[tuple[Volume, str]] = []
        for v in camp.volumes:
            st = derive(v, pid, done, jobs, budgets, cfg.attempt_cap)
            public_src, job_src = _source_manifest_url(v, pid, bucket, cfg)
            akey = _attempt_key(pid, v.id)
            # The thumbnail is read from the cache unconditionally — a done
            # volume still needs its picture, and a cache hit costs nothing. Only
            # the FETCH and the status override are gated.
            cached = validation.get(v.manifest_url) if v.manifest_url else None
            thumb = cached.get("thumbnail") if cached else None
            if v.manifest_url and fetch_json is not None and st != "done":
                verdict = _validate(v.manifest_url, validation, fetch_json)
                thumb = verdict["thumbnail"]
                if verdict["format"] in _BLOCKING_VERDICTS:
                    st = verdict["format"]  # no job burned (spec §4.4)
            # Cleanup and the attempt bump are gated on the pipeline too: a
            # drift-blocked pipeline submits nothing, so it must not spend the
            # volume's retry budget while the operator sorts the drift out.
            if st == "retry" and pid not in blocked:
                name = job_name(pid, v.id)
                bucket.put_text(
                    keys.failure_log_key(pid, v.id), cluster.job_logs(name)
                )
                cluster.delete_job(name)
                attempts[akey] = attempts.get(akey, 0) + 1
                budgets[v.id] = attempts[akey]
                lane.append((v, job_src))
            elif st == "pending" and pid not in blocked:
                lane.append((v, job_src))
            elif st == "needs-attention" and job_name(pid, v.id) in jobs:
                # The retry path uploads logs before deleting the Job; an
                # exit-13 (or capped) volume otherwise reaches its terminal
                # state with no uploaded evidence. Idempotent overwrite.
                bucket.put_text(
                    keys.failure_log_key(pid, v.id),
                    cluster.job_logs(job_name(pid, v.id)),
                )
            if st == "done":
                entry["totals"]["done"] += 1
            pages_done = (
                bucket.count_pages(pid, v.id) if st in ("done", "running") else None
            )
            if v.images:
                pages_total: int | None = len(v.images)
            else:
                cached_v = validation.get(v.manifest_url) if v.manifest_url else None
                pages_total = cached_v.get("page_count") if cached_v else None
            if pages_total is None and st == "done":
                pages_total = pages_done
            run_log = None
            if st == "done":
                log_key = keys.run_log_key(pid, v.id)
                job = jobs.get(job_name(pid, v.id))
                if bucket.exists(log_key):
                    run_log = f"{cfg.public_results_base.rstrip('/')}/{log_key}"
                elif job is not None and job.succeeded:
                    # Jobs linger ttlSecondsAfterFinished (24h) after Complete —
                    # one upload per volume, guarded by the HEAD above.
                    bucket.put_text(
                        log_key, cluster.job_logs(job_name(pid, v.id), tail=500)
                    )
                    run_log = f"{cfg.public_results_base.rstrip('/')}/{log_key}"
            entry["volumes"].append(
                {
                    "id": v.id,
                    "status": st,
                    "attempts": attempts.get(akey, 0),
                    "updated": done.get(v.id),
                    "failure_log": (
                        f"{cfg.public_results_base.rstrip('/')}/"
                        f"{keys.failure_log_key(pid, v.id)}"
                        if st in ("retry", "needs-attention")
                        else None
                    ),
                    "run_log": run_log,
                    "pages_done": pages_done,
                    "pages_total": pages_total,
                    "error": None,
                    "viewer_manifest": (
                        f"{cfg.public_results_base.rstrip('/')}/{pid}/{v.id}/iiif.json"
                        if st == "done"
                        else None
                    ),
                    "source_manifest": public_src,
                    "thumbnail": thumb,
                }
            )
        known_totals = [
            v["pages_total"] for v in entry["volumes"] if v["pages_total"] is not None
        ]
        known_done = [
            v["pages_done"] for v in entry["volumes"] if v["pages_done"] is not None
        ]
        entry["totals"]["pages_total"] = sum(known_totals) if known_totals else None
        entry["totals"]["pages_done"] = sum(known_done) if known_done else None
        if pid not in blocked and lane:
            pending[camp.name] = lane

    lanes = {name: [v for v, _ in lane] for name, lane in pending.items()}
    srcs = {(n, v.id): s for n, lane in pending.items() for v, s in lane}
    for camp_name, volume in plan_submissions(lanes, in_flight, cfg.window):
        camp = next(c for c in campaigns if c.name == camp_name)
        spec = pipelines[camp.pipeline_id]
        cluster.create_job(build_job(spec, volume, srcs[(camp_name, volume.id)], cfg))

    bucket.write_json(keys.validation_key(), validation)
    bucket.write_json(keys.attempts_key(), attempts)
    bucket.write_json(keys.status_key(), doc)
    return doc
