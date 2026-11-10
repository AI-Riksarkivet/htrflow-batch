"""``retry``: which volumes of a campaign are unfinished, and why; and the
retry campaign that runs them again, built from the original's record."""

from __future__ import annotations

import contextlib
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from htrflow_converter import render
from htrflow_converter.export import _safe_segment
from htrflow_converter.export_http import Results
from htrflow_converter.models import STATUS_SUFFIX, WARMUP_PREFIX, parse_source_line

if TYPE_CHECKING:
    from htrflow_converter.cluster import Cluster

_PERMANENT = "permanent failure in "
_TRANSIENT = "transient failure in "
_LAST_ATTEMPT = " on the last attempt"
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_GROUP_MAX = 100


@dataclass(frozen=True)
class Outcome:
    id: str
    line: str  # the original ``volumes.txt`` line, unchanged
    state: Literal["done", "retry", "permanent"]
    reason: str


def classify_log(text: str | None) -> tuple[bool, str]:
    """``(permanent, reason)`` from a volume's run log. The LAST failure line
    decides, not the last line: a traceback may follow it."""
    if text is None:
        return False, "no run log: the volume never started"
    found = None
    for raw in text.splitlines():
        for marker in (_PERMANENT, _TRANSIENT):
            if marker in raw:
                found = (marker == _PERMANENT, raw.partition(marker)[2])
    if found is None:
        return False, "no failure line in the run log"
    permanent, rest = found
    return permanent, rest.partition(" — ")[0].replace(_LAST_ATTEMPT, "")


def reason_group(reason: str) -> str:
    """The reason with each quoted span folded, so that failures differing only
    in a path or a name count as one group; capped to stay readable."""
    return _QUOTED.sub("'…'", reason)[:_GROUP_MAX]


def _outcome(results: Results, namespace: str, pipeline: str, line: str) -> Outcome:
    vid, _ = parse_source_line(line)
    if not _safe_segment(vid):
        return Outcome(vid, line, "retry", "id not readable as a results path")
    seg = quote(vid, safe="")
    if results.get(f"{namespace}/{pipeline}/{seg}/manifest.json") is not None:
        return Outcome(vid, line, "done", "")
    log = results.get(f"{namespace}/status/logs/{pipeline}/{seg}.txt")
    permanent, reason = classify_log(
        log.decode("utf-8", "replace") if log is not None else None
    )
    return Outcome(vid, line, "permanent" if permanent else "retry", reason)


def find_unfinished(
    results: Results,
    namespace: str,
    pipeline: str,
    lines: list[str],
    workers: int = 16,
) -> list[Outcome]:
    """One ``Outcome`` per line, in input order. A volume is done when its
    manifest exists; otherwise its log says why not. ``Forbidden``,
    ``SessionLost`` and ``ResultsUnavailable`` end the search."""
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [
            pool.submit(_outcome, results, namespace, pipeline, line) for line in lines
        ]
        return [f.result() for f in futures]
    finally:
        # On the first error the fetches still queued are never started.
        pool.shutdown(wait=True, cancel_futures=True)


# ``.cluster`` is imported inside the functions below, never here: it pulls
# in ``kubernetes``, and ``cli`` imports this module for commands that never
# reach a cluster (the same reason ``cli._cluster`` imports it lazily).


@dataclass
class Original:
    """The campaign a retry restarts volumes of, as its record holds it."""

    #: What a retry's ``RETRY_OF_LABEL`` carries: the first part's Job name
    #: (the ``<name>`` of ``campaign-<name>``, which prune keeps it by), or a
    #: retry's own ``retry-of`` when this is a retry, so a chain prunes as one.
    root: str
    #: Its Job names, in part order.
    parts: list[str]
    pipeline: str
    digest: str
    #: Every part's ``volumes.txt`` lines, blank ones dropped, in part order.
    lines: list[str]
    #: The Job a retry's Jobs are copied from.
    source_job: dict
    #: The first part ConfigMap's labels, which a retry's ConfigMaps keep.
    record_labels: dict[str, str] = field(default_factory=dict)
    #: The other campaign whose Job ``source_job`` was copied from, when this
    #: one's own is gone and was never stamped; ``""`` when it is its own.
    settings_from: str = ""
    #: The pipeline the campaign ran on, when ``on_pipeline`` moved it to
    #: another; ``""`` when it stays where it ran.
    moved_from: str = ""


_PART = re.compile(r"-part(\d+)$")
_RETRY_N = re.compile(r"-retry-(\d+)$")
#: What the Job controller labels a Job and its pod template with, to tie
#: the two together: a copy that kept them would give the retry's pods the
#: original's uid and name. ``spec.selector`` is dropped for the same reason.
_CONTROLLER_LABELS = ("controller-uid", "job-name")
_CONTROLLER_PREFIX = "batch.kubernetes.io/"
#: What Kueue writes on a pod template at admission (render puts none there).
#: It stays once the Job ends, and Kueue finishes a copy carrying another
#: workload's name without starting it.
_KUEUE_PREFIX = "kueue.x-k8s.io/"
_NO_STAMP = (
    "campaign {name} was applied before retries existed and its Job is gone, "
    "and no other campaign on pipeline {pipeline} with image {digest} has a "
    "Job or a stored one to copy the settings from — the next apply by this "
    "converter version records them while its campaign file is in the repo"
)
_OLD_PRUNE = (
    "the last apply in namespace {ns} ran a converter whose prune does not "
    "know retries — upgrade the Argo CD hook's converter and let it apply once"
)
_RUNNING = (
    "campaign {job} is still running in namespace {ns} — let it finish before "
    "retrying {root}"
)
_NO_WARMUP = (
    "pipeline {pipeline}'s warm-up Job {job} is {state} — retry once it has completed"
)
_MOVED = (
    "pipeline {pipeline}'s warm-up Job {job} no longer runs image {digest}, "
    "the one campaign {root} ran — a retry runs only what the repo approved, "
    "so run the volumes as a new campaign file"
)
_UNKNOWN = (
    "campaign {job} is running on pipeline {pipeline} and its ConfigMap "
    "campaign-{job} has no volumes.txt to read, so it may be running these "
    "volumes — let it finish first"
)
_HELD = (
    "campaign {job} is running {volumes} on pipeline {pipeline}: both would "
    "write the same results — let it finish first"
)


def _condition(job: dict, *types: str) -> bool:
    """``cli._condition``, which this module cannot import (``cli`` imports
    it)."""
    conditions = (job.get("status") or {}).get("conditions") or []
    return any(c.get("type") in types and c.get("status") == "True" for c in conditions)


def _labels(obj: dict) -> dict[str, str]:
    return (obj.get("metadata") or {}).get("labels") or {}


def _lines(cm: dict) -> list[str]:
    """``volumes.txt`` as volume lines: what ``cli._volume_list`` reads."""
    text = (cm.get("data") or {}).get("volumes.txt") or ""
    return [line for line in text.splitlines() if line]


def _part_order(cm: dict) -> tuple[int, str]:
    """``-part2`` before ``-part10``: by name alone it would be after."""
    name = cm["metadata"]["name"]
    m = _PART.search(name)
    return (int(m.group(1)) if m else 0, name)


def _uncontrolled(labels: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in labels.items()
        if k not in _CONTROLLER_LABELS and not k.startswith(_CONTROLLER_PREFIX)
    }


def _as_applied(live: dict) -> dict:
    """A live Job cut back to what an apply sends: no status, no server
    metadata, and none of what the Job controller added to tie the Job to
    its pods, nor what Kueue added on admitting it -- a copy sent with those
    would claim the original's."""
    meta = live.get("metadata") or {}
    job = {
        "apiVersion": live.get("apiVersion", "batch/v1"),
        "kind": "Job",
        "metadata": {
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "labels": _uncontrolled(meta.get("labels") or {}),
            "annotations": _uncontrolled(meta.get("annotations") or {}),
        },
        "spec": copy.deepcopy(live.get("spec") or {}),
    }
    job["spec"].pop("selector", None)
    job["spec"].pop("manualSelector", None)
    tmeta = job["spec"].get("template", {}).get("metadata")
    if tmeta is not None:
        for key in ("labels", "annotations"):
            kept = {
                k: v
                for k, v in _uncontrolled(tmeta.pop(key, None) or {}).items()
                if not k.startswith(_KUEUE_PREFIX)
            }
            if kept:
                tmeta[key] = kept
    return job


def load_original(cluster: Cluster, campaign: str) -> Original:
    """The campaign file ``campaign`` (or a retry's name) as its ConfigMaps
    record it, every part. The Job to copy is the one ``apply`` stamped on
    the first part's ConfigMap; a campaign applied before that stamp falls
    back to its live Job, while the TTL has not reaped it."""
    from .cluster import ClusterError

    label = render.label_value(campaign)
    parts = sorted(
        (
            cm
            for cm in cluster.labelled("ConfigMap")
            if _labels(cm).get(render.CAMPAIGN_LABEL) == label
            and _labels(cm).get(render._KIND_LABEL) != render._STATUS_KIND
            # A status record written before it carried the kind label.
            and not cm["metadata"]["name"].endswith(STATUS_SUFFIX)
        ),
        key=_part_order,
    )
    if not parts:
        raise ClusterError(
            f"no campaign {campaign} in namespace {cluster.namespace}: no "
            f"campaign ConfigMap carries {render.CAMPAIGN_LABEL}={label}"
        )
    first = parts[0]
    names = [cm["metadata"]["name"].removeprefix("campaign-") for cm in parts]
    labels = _labels(first)
    annotations = first["metadata"].get("annotations") or {}
    stamped = annotations.get(render.JOB_SPEC_ANNOTATION)
    if stamped:
        try:
            source = json.loads(stamped)
        except json.JSONDecodeError:
            source = None
        if not isinstance(source, dict) or not isinstance(source.get("spec"), dict):
            raise ClusterError(
                f"ConfigMap campaign-{names[0]}'s {render.JOB_SPEC_ANNOTATION} "
                "annotation is not a Job, so there are no settings to copy — "
                "let the next apply stamp it again, then retry"
            )
    else:
        live = cluster.get("Job", names[0])
        source = None if live is None else _as_applied(live)
    pipeline = labels.get(render._PIPELINE_LABEL, "")
    digest = annotations.get(render._DIGEST_ANNOTATION, "")
    settings_from = ""
    if source is None:
        source, settings_from = _sibling(cluster, set(names), pipeline, digest)
        if source is None:
            raise ClusterError(
                _NO_STAMP.format(name=campaign, pipeline=pipeline, digest=digest)
            )
    containers = (
        (source.get("spec") or {}).get("template", {}).get("spec", {}).get("containers")
    )
    if not isinstance(containers, list) or not containers:
        raise ClusterError(
            f"the Job copied for campaign {campaign} has no container, so "
            "there are no settings to copy — let the next apply stamp it "
            "again, then retry"
        )
    return Original(
        root=labels.get(render.RETRY_OF_LABEL) or names[0],
        parts=names,
        pipeline=pipeline,
        digest=digest,
        lines=[line for cm in parts for line in _lines(cm)],
        source_job=source,
        record_labels=dict(labels),
        settings_from=settings_from,
    )


def _image(job: dict) -> str:
    pod = (job.get("spec") or {}).get("template", {}).get("spec", {})
    return ((pod.get("containers") or [{}])[0]).get("image", "")


def _sibling(
    cluster: Cluster, own: set[str], pipeline: str, digest: str
) -> tuple[dict | None, str]:
    """Another campaign's Job on the same pipeline and image, as an apply
    sends it, and its name: a live one, else one stamped on its ConfigMap,
    the newest first. Everything in a campaign Job but its name, counts,
    volume list, window and priority comes from the pipeline and
    converter.yaml, and ``retry_objects`` rewrites the first three. The
    priority was that campaign's own choice, so it is dropped: the retry
    runs at the default class."""
    if not pipeline or not digest:
        return None, ""
    label = render.label_value(pipeline)

    def ours(obj: dict) -> bool:
        lb = _labels(obj)
        return (
            lb.get(render._PIPELINE_LABEL) == label
            and render.CAMPAIGN_LABEL in lb
            and lb.get(render._KIND_LABEL) != render._STATUS_KIND
        )

    def newest(obj: dict) -> str:
        return (obj.get("metadata") or {}).get("creationTimestamp") or ""

    found: list[tuple[dict, str]] = []
    for job in sorted(cluster.labelled("Job"), key=newest, reverse=True):
        if ours(job) and job["metadata"]["name"] not in own:
            found.append((_as_applied(job), job["metadata"]["name"]))
    for cm in sorted(cluster.labelled("ConfigMap"), key=newest, reverse=True):
        name = cm["metadata"]["name"].removeprefix("campaign-")
        stamp = (cm["metadata"].get("annotations") or {}).get(
            render.JOB_SPEC_ANNOTATION
        )
        if not (ours(cm) and stamp and name not in own):
            continue
        try:
            job = json.loads(stamp)
        except json.JSONDecodeError:
            continue
        if isinstance(job, dict) and isinstance(job.get("spec"), dict):
            found.append((job, name))
    for job, name in found:
        if _image(job) == digest:
            job["metadata"].setdefault("labels", {}).pop(render._PRIORITY_LABEL, None)
            return job, name
    return None, ""


_NO_PIPELINE = (
    "no pipeline {pipeline} in namespace {ns}: {missing} is missing — a "
    "retry runs only a pipeline the repo has applied here"
)
_OTHER_SIZE = (
    "pipeline {pipeline} runs at size {new} and {old} at {old_size}: what a "
    "size gives a pod is in converter.yaml, which a retry does not read, and "
    "no campaign has run on {pipeline} to copy it from"
)


def _size_of(cluster: Cluster, pipeline: str) -> str | None:
    """The size ``pipeline`` runs at, ``"(none)"`` for the skeleton's, and
    ``None`` when its ConfigMap is gone, so it is not known."""
    cm = cluster.get("ConfigMap", f"htr-pipeline-{pipeline}")
    if cm is None:
        return None
    annotations = (cm.get("metadata") or {}).get("annotations") or {}
    return annotations.get(render._SIZE_ANNOTATION) or "(none)"


def _moved(job: dict, old: str, new: str, warmup: dict) -> dict:
    """``job`` with what ``render._campaign_job`` takes from the pipeline set
    for ``new``, from its warm-up Job: the label, the image, the env that
    names them, the pipeline ConfigMap, the model-cache directory and the
    warm-up marker. The deadline and TTL stay: a pipeline file's own are
    not on the cluster."""
    job = copy.deepcopy(job)
    image = _image(warmup)
    wpod = warmup["spec"]["template"]["spec"]
    subpath = next(
        (
            m.get("subPath")
            for c in wpod["containers"]
            for m in c.get("volumeMounts") or []
            if m.get("name") == "data"
        ),
        None,
    )
    job["metadata"]["labels"][render._PIPELINE_LABEL] = render.label_value(new)
    pod = job["spec"]["template"]["spec"]
    env = {"PIPELINE_ID": new, "IMAGE_DIGEST": image}
    old_marker, new_marker = f"/data/warmup/{old}.done", f"/data/warmup/{new}.done"
    # The two render gives the pipeline's image: the wrapper and the gate.
    pod["containers"][0]["image"] = image
    if pod.get("initContainers"):
        pod["initContainers"][0]["image"] = image
    for c in (pod.get("initContainers") or []) + pod["containers"]:
        c["command"] = [
            a.replace(old_marker, new_marker) for a in c.get("command") or []
        ]
        for e in c.get("env") or []:
            if e.get("name") in env:
                e["value"] = env[e["name"]]
        for m in c.get("volumeMounts") or []:
            if m.get("name") == "data":
                m.pop("subPath", None)
                if subpath:
                    m["subPath"] = subpath
    for volume in pod.get("volumes") or []:
        if volume.get("name") == "pipeline":
            volume["configMap"]["name"] = f"htr-pipeline-{new}"
    return job


def on_pipeline(cluster: Cluster, original: Original, pipeline: str) -> Original:
    """``original`` to run on ``pipeline`` instead, which the repo applied
    here. Its settings come from a campaign that ran on ``pipeline`` and its
    image (``_sibling``), as everything the pipeline decides is right there,
    with ``original``'s priority; else ``original``'s Job is moved onto it
    (``_moved``), when the size it runs at is the same."""
    from .cluster import ClusterError

    if pipeline == original.pipeline:
        return original
    cm_name, warmup_name = f"htr-pipeline-{pipeline}", f"{WARMUP_PREFIX}{pipeline}"
    warmup = cluster.get("Job", warmup_name)
    missing = [f"Job {warmup_name}"] if warmup is None else []
    if cluster.get("ConfigMap", cm_name) is None:
        missing.insert(0, f"ConfigMap {cm_name}")
    if warmup is None or missing:
        raise ClusterError(
            _NO_PIPELINE.format(
                pipeline=pipeline, ns=cluster.namespace, missing=" and ".join(missing)
            )
        )
    digest = _image(warmup)
    job, settings_from = _sibling(cluster, set(original.parts), pipeline, digest)
    if job is not None:
        priority = _labels(original.source_job).get(render._PRIORITY_LABEL)
        if priority:
            job["metadata"]["labels"][render._PRIORITY_LABEL] = priority
    else:
        old_size, new = (
            _size_of(cluster, original.pipeline),
            _size_of(cluster, pipeline),
        )
        if old_size != new:
            raise ClusterError(
                _OTHER_SIZE.format(
                    pipeline=pipeline,
                    new=new,
                    old=original.pipeline,
                    old_size=old_size
                    or "a size no longer known (its ConfigMap is gone)",
                )
            )
        job = _moved(original.source_job, original.pipeline, pipeline, warmup)
    labels = {
        **original.record_labels,
        render._PIPELINE_LABEL: render.label_value(pipeline),
    }
    return replace(
        original,
        pipeline=pipeline,
        digest=digest,
        source_job=job,
        record_labels=labels,
        settings_from=settings_from,
        moved_from=original.pipeline,
    )


def _volumes(ids: list[str]) -> str:
    return (
        ("volume " if len(ids) == 1 else "volumes ")
        + ", ".join(ids[:3])
        + (f" and {len(ids) - 3} more" if len(ids) > 3 else "")
    )


def old_prune_refusal(cluster: Cluster) -> str | None:
    """One sentence when the last apply here ran a converter whose prune does
    not know retries (it would delete the retry), else ``None``. Asked before
    the Lease is taken, never inside it: a holder writes its own marks back,
    so inside the Lease they always say yes."""
    from .cluster import PRUNES_RETRIES

    if PRUNES_RETRIES in cluster.lease_marks():
        return None
    return _OLD_PRUNE.format(ns=cluster.namespace)


def refusal(cluster: Cluster, original: Original, volume_ids: set[str]) -> str | None:
    """One sentence when this retry must not start, else ``None``. In order:
    the campaign (or an earlier retry of it) still runs; the warm-up is not
    done or runs another image than the original did; another running
    campaign on the pipeline holds a volume. Whether an apply here would
    prune the retry is ``old_prune_refusal``, asked before the Lease."""
    ns = cluster.namespace
    running = [
        j
        for j in cluster.labelled("Job")
        if not j["metadata"]["name"].startswith(WARMUP_PREFIX)
        and not _condition(j, "Complete", "Failed")
    ]
    # The root's own campaign too, when this is a retry of a retry: its
    # other parts are not among ``original.parts``.
    root_cm = cluster.get("ConfigMap", f"campaign-{original.root}") or {}
    root_campaign = _labels(root_cm).get(render.CAMPAIGN_LABEL)
    for job in running:
        name, labels = job["metadata"]["name"], _labels(job)
        if (
            name in original.parts
            or labels.get(render.RETRY_OF_LABEL) == original.root
            or (root_campaign and labels.get(render.CAMPAIGN_LABEL) == root_campaign)
        ):
            return _RUNNING.format(job=name, ns=ns, root=original.root)
    warmup = f"{WARMUP_PREFIX}{original.pipeline}"
    live = cluster.get("Job", warmup)
    if live is None or not _condition(live, "Complete"):
        state = "missing" if live is None else "not complete"
        return _NO_WARMUP.format(pipeline=original.pipeline, job=warmup, state=state)
    # The warm-up carries the pipeline's image; its ConfigMap only the steps.
    if render.recipe([live])["image"] != original.digest:
        return _MOVED.format(
            pipeline=original.pipeline,
            job=warmup,
            digest=original.digest or "(none recorded)",
            root=original.root,
        )
    for job in running:
        if _labels(job).get(render._PIPELINE_LABEL) != original.pipeline:
            continue
        name = job["metadata"]["name"]
        cm = cluster.get("ConfigMap", f"campaign-{name}") or {}
        if "volumes.txt" not in (cm.get("data") or {}):
            # Fails closed: a volume list that cannot be read may hold any.
            return _UNKNOWN.format(job=name, pipeline=original.pipeline)
        shared = sorted(
            volume_ids & {parse_source_line(line)[0] for line in _lines(cm)}
        )
        if shared:
            return _HELD.format(
                job=name, volumes=_volumes(shared), pipeline=original.pipeline
            )
    return None


def next_name(cluster: Cluster, root: str) -> str:
    """``<root>-retry-<n>``, ``n`` one past the highest retry of ``root`` in
    the cluster, cut to the stem a split campaign's parts are built from.
    Read off the retry's campaign label, not its object names: a split
    retry's are ``-partN``.

    The cut makes two long roots that share a prefix -- or a campaign file
    that is itself called ``<x>-retry-1`` -- spell the same name, so ``n``
    also goes past every labelled object whose campaign is that stem's
    retry, whatever its ``retry-of`` says, and past any name in use.
    ``start`` still refuses a name that is taken: this only picks a free one.
    """
    objects = [o for kind in ("ConfigMap", "Job") for o in cluster.labelled(kind)]
    highest = 0
    for obj in objects:
        labels = _labels(obj)
        m = _RETRY_N.search(labels.get(render.CAMPAIGN_LABEL, ""))
        if m is None:
            continue
        n = int(m.group(1))
        if labels.get(render.RETRY_OF_LABEL) == root or _named(root, n) == m.string:
            highest = max(highest, n)
    taken = {_labels(o).get(render.CAMPAIGN_LABEL) for o in objects} | {
        _PART.sub(
            "",
            o["metadata"]["name"].removeprefix("campaign-").removesuffix(STATUS_SUFFIX),
        )
        for o in objects
    }
    n = highest + 1
    while _named(root, n) in taken:
        n += 1
    return _named(root, n)


def _named(root: str, n: int) -> str:
    suffix = f"-retry-{n}"
    return f"{root[: render._SPLIT_STEM - len(suffix)]}{suffix}"


#: On a retry's first ConfigMap, the volumes it left out (permanent
#: failures), one id per line, and how many there were. Annotations, not a
#: ``data`` key: annotations are not mounted into the pod.
SKIPPED_ANNOTATION = "htrflow.riksarkivet.se/retry-skipped"
SKIPPED_COUNT_ANNOTATION = "htrflow.riksarkivet.se/retry-skipped-count"
#: Bytes the skipped list may take. The API server refuses an object whose
#: annotations together pass 256 KiB, and the ``job-spec`` stamp beside this
#: one is a whole Job; the count annotation keeps the full number.
SKIPPED_MAX_BYTES = 64 * 1024


def _skipped_value(skipped: list[str]) -> str:
    """``skipped`` one per line, cut to ``SKIPPED_MAX_BYTES`` with a last
    line saying how many more there were."""
    text = "\n".join(skipped)
    if len(text.encode()) <= SKIPPED_MAX_BYTES:
        return text
    # Room for the widest tail this list can need.
    budget = SKIPPED_MAX_BYTES - len(f"\n… and {len(skipped)} more".encode())
    kept: list[str] = []
    used = 0
    for vid in skipped:
        size = len(vid.encode()) + (1 if kept else 0)
        if used + size > budget:
            break
        kept.append(vid)
        used += size
    return "\n".join([*kept, f"… and {len(skipped) - len(kept)} more"])


def _split(lines: list[str]) -> list[list[str]]:
    """``render.split``'s cut, by its own budgets, over lines as they stand:
    rebuilding ``Volume``s from them could re-spell a line the original
    Job ran."""
    parts: list[list[str]] = [[]]
    used = 0
    for line in lines:
        size_of = len(line.encode()) + 1
        if parts[-1] and (
            len(parts[-1]) >= render.MAX_VOLUMES_PER_JOB
            or used + size_of > render.MAX_BYTES_PER_JOB
        ):
            parts.append([])
            used = 0
        parts[-1].append(line)
        used += size_of
    return parts


def retry_objects(
    original: Original,
    name: str,
    lines: list[str],
    skipped: list[str],
    prov: dict[str, str],
) -> list[dict]:
    """The retry's ConfigMap and Job per part, in apply order. The Job is the
    original's with only what makes it another campaign changed: its name,
    its labels' campaign and ``retry-of``, its counts and its ConfigMap."""
    if not lines:
        raise ValueError("a retry needs at least one volume line")
    chunks = _split(lines)
    names = (
        [name]
        if len(chunks) == 1
        else [f"{name}-part{i}" for i in range(1, len(chunks) + 1)]
    )
    campaign = render.label_value(name)
    namespace = original.source_job.get("metadata", {}).get("namespace", "")
    objects: list[dict] = []
    for i, (part, chunk) in enumerate(zip(names, chunks)):
        job = copy.deepcopy(original.source_job)
        job["metadata"]["name"] = part
        job["metadata"]["labels"][render.CAMPAIGN_LABEL] = campaign
        job["metadata"]["labels"][render.RETRY_OF_LABEL] = original.root
        spec = job["spec"]
        spec["completions"] = spec["maxFailedIndexes"] = len(chunk)
        spec["parallelism"] = min(spec.get("parallelism", 1), len(chunk))
        # Kueue suspends a queued Job on admission; a paused original's
        # ``suspend: true`` would leave its retry waiting for a resume that
        # no campaign file can send.
        spec.pop("suspend", None)
        repointed = False
        for volume in spec["template"]["spec"].get("volumes") or []:
            if volume.get("name") == "campaign":
                volume["configMap"]["name"] = f"campaign-{part}"
                repointed = True
        if not repointed:
            # Otherwise the retry would mount the original's ConfigMap.
            raise ValueError(
                f"the Job copied for {original.root} has no pod volume named "
                "campaign, so a retry could not be given its own volume list"
            )
        labels = dict(original.record_labels)
        labels[render.CAMPAIGN_LABEL] = campaign
        labels[render.RETRY_OF_LABEL] = original.root
        annotations = {
            render._DIGEST_ANNOTATION: original.digest,
            **prov,
            render.JOB_SPEC_ANNOTATION: json.dumps(
                job, sort_keys=True, separators=(",", ":")
            ),
        }
        if i == 0 and skipped:  # the record of what was left out, once
            annotations[SKIPPED_ANNOTATION] = _skipped_value(skipped)
            annotations[SKIPPED_COUNT_ANNOTATION] = str(len(skipped))
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"campaign-{part}",
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            # Only ``volumes.txt``: the Job mounts this ConfigMap whole, so
            # any other key is a file in the pod, and the chart's
            # ``campaign-configmap-keys`` policy refuses it.
            "data": {"volumes.txt": "".join(f"{line}\n" for line in chunk)},
        }
        objects += [cm, job]
    return objects


def start(cluster: Cluster, objects: list[dict]) -> None:
    """Every Job tried with a dry run first, then each part's ConfigMap and
    Job. A ConfigMap sent under a Job the API server then refuses is the
    record of a retry that never ran, which the next retry would number past
    and a prune would keep."""
    from .cluster import ClusterError

    # Every object is a create: ``apply`` is a forced server-side apply, and
    # an existing labelled object of that name -- another campaign's record,
    # a retry whose Job was reaped -- would be taken over, not refused.
    for obj in objects:
        kind, name = obj["kind"], obj["metadata"]["name"]
        if cluster.get(kind, name) is not None:
            raise ClusterError(
                f"{kind} {name} already exists in namespace {cluster.namespace}, "
                "so the retry was not started and nothing was written — it "
                "belongs to another campaign or retry; run the retry again to "
                "be given a free name"
            )
    for obj in objects:
        if obj["kind"] == "Job":
            cluster.apply(obj, dry_run=True)
    made: list[dict] = []
    for obj in objects:
        try:
            cluster.apply(obj)
        except ClusterError as exc:
            # A ConfigMap just written for a Job that was refused is the
            # record of a retry that never ran: take it back.
            if obj["kind"] == "Job" and made and made[-1]["kind"] == "ConfigMap":
                cm = made.pop()
                with contextlib.suppress(ClusterError):
                    cluster.delete_configmap(cm["metadata"]["name"])
            live = ", ".join(f"{o['kind']}/{o['metadata']['name']}" for o in made)
            left = (
                f"already created and still live: {live}"
                if live
                else "nothing is left behind"
            )
            raise ClusterError(
                f"{exc} — the retry stopped at {obj['kind']}/"
                f"{obj['metadata']['name']}; {left}"
            ) from exc
        made.append(obj)
