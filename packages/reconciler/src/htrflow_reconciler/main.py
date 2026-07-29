"""One reconcile pass (docs: how-it-works/campaigns). Pure orchestration:
adapters injected, no I/O of its own beyond them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import s3 as keys
from .guards import check_drift
from .jobspec import ReconcilerConfig, build_job
from .models import PipelineSpec, Volume
from .parse import PipelineError, parse_campaign, parse_pipeline
from .plan import plan_submissions
from .status import derive, job_name
from .synthetic import build_manifest, classify_manifest

TICK_SECONDS = 300

#: Verdicts that mean "this source can never produce a job" (spec §4.4).
_BLOCKING_VERDICTS = ("unreachable", "unsupported")


def _load_repo(campaigns_dir: Path):
    campaigns = [
        parse_campaign(p.stem, p.read_text())
        for p in sorted((campaigns_dir / "campaigns").glob("*.yaml"))
    ]
    pipelines: dict[str, PipelineSpec] = {}
    errors: list[str] = []
    for p in sorted((campaigns_dir / "pipelines").glob("*.yaml")):
        try:
            pipelines[p.stem] = parse_pipeline(p.stem, p.read_text())
        except PipelineError as e:
            errors.append(str(e))
    return campaigns, pipelines, errors


def _source_manifest_url(
    volume: Volume, pipeline_id: str, bucket, cfg: ReconcilerConfig
) -> str:
    if volume.manifest_url:
        return volume.manifest_url
    key = keys.synthetic_manifest_key(pipeline_id, volume.id)
    url = f"{cfg.public_results_base.rstrip('/')}/{key}"
    if bucket.read_json(key) is None:
        bucket.write_json(key, build_manifest(volume.id, list(volume.images), url))
    return url


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
    """Once-ever verdict per manifest URL (spec §4.4): format + thumbnail."""
    if url in cache:
        return cache[url]
    doc = fetch_json(url)
    if doc is None:
        verdict = {"format": "unreachable", "thumbnail": None}
    else:
        verdict = {"format": _classify(doc), "thumbnail": _thumbnail(doc)}
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
    done_cache: dict[str, set[str]] = {}

    def done_for(pipeline_id: str) -> set[str]:
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

    doc: dict[str, Any] = {
        "generated_at": now_iso,
        "tick_seconds": TICK_SECONDS,
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
    in_flight = sum(1 for j in jobs.values() if not j.failed)

    for camp in campaigns:
        entry: dict[str, Any] = {
            "name": camp.name,
            "pipeline": camp.pipeline_id or None,
            "error": camp.error,
            "totals": {"done": 0, "total": len(camp.volumes)},
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
            entry["orphans"] = sorted(done - claimed[pid])
            orphans_reported.add(pid)
        lane: list[tuple[Volume, str]] = []
        for v in camp.volumes:
            st = derive(v, pid, done, jobs, attempts, cfg.attempt_cap)
            src = _source_manifest_url(v, pid, bucket, cfg)
            thumb = None
            if v.manifest_url and fetch_json is not None and st != "done":
                verdict = _validate(v.manifest_url, validation, fetch_json)
                thumb = verdict["thumbnail"]
                if verdict["format"] in _BLOCKING_VERDICTS:
                    st = verdict["format"]  # no job burned (spec §4.4)
            if st == "retry":
                name = job_name(pid, v.id)
                bucket.put_text(
                    keys.failure_log_key(pid, v.id), cluster.failed_job_logs(name)
                )
                cluster.delete_job(name)
                attempts[v.id] = attempts.get(v.id, 0) + 1
                lane.append((v, src))
            elif st == "pending" and pid not in blocked:
                lane.append((v, src))
            if st == "done":
                entry["totals"]["done"] += 1
            entry["volumes"].append(
                {
                    "id": v.id,
                    "status": st,
                    "attempts": attempts.get(v.id, 0),
                    "pages_done": bucket.count_pages(pid, v.id)
                    if st in ("done", "running")
                    else None,
                    "pages_total": None,
                    "error": None,
                    "viewer_manifest": (
                        f"{cfg.public_results_base.rstrip('/')}/{pid}/{v.id}/iiif.json"
                        if st == "done"
                        else None
                    ),
                    "source_manifest": src,
                    "thumbnail": thumb,
                }
            )
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
