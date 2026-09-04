"""Pure projections of Kubernetes API dicts onto the ``/api/v1/jobs`` shapes.

Every function here takes plain dicts — what ``kube.Reader`` reads back off
the API server, or what a test builds by hand — and never touches the
cluster or the network. That is what makes these testable without a fixture
cluster (docs: task-4-brief).
"""

from __future__ import annotations

import json

import yaml

_PIPELINE_LABEL = "htrflow.riksarkivet.se/pipeline"
_INDEX_LABEL = "batch.kubernetes.io/job-completion-index"
_MAX_FAILURES = 50


def parse_index_ranges(spec: str | None) -> set[int]:
    """``"0-2,5,7-9"`` -> ``{0,1,2,5,7,8,9}``. ``""``/``None`` -> empty set.

    Written once here; every caller (``done``/``failed`` counts, per-volume
    state) goes through it.
    """
    out: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out


def _labels(obj: dict) -> dict:
    return (obj.get("metadata") or {}).get("labels") or {}


def configmap_ref(job: dict, volume: str = "campaign") -> str | None:
    """Name of the ConfigMap mounted as one of the Job's volumes: ``campaign``
    holds ``volumes.txt``, ``pipeline`` holds ``pipeline.yaml``. Reading the
    name off the pod spec rather than rebuilding ``htr-pipeline-<id>`` keeps
    the naming convention in the converter, where it is rendered."""
    pod_spec = ((job.get("spec") or {}).get("template") or {}).get("spec") or {}
    for v in pod_spec.get("volumes") or []:
        if v.get("name") == volume:
            return (v.get("configMap") or {}).get("name")
    return None


def _phase(job: dict) -> str:
    status = job.get("status") or {}
    conditions = status.get("conditions") or []
    if any(
        c.get("type") == "Complete" and c.get("status") == "True" for c in conditions
    ):
        return "Succeeded"
    if any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions):
        # A Job that gave up still keeps whatever its completed indexes
        # published: a plain "Failed" would read as "nothing came out of this
        # campaign", which is wrong whenever completedIndexes is non-empty.
        done = len(parse_index_ranges(status.get("completedIndexes")))
        return "PartiallyFailed" if done else "Failed"
    if bool((job.get("spec") or {}).get("suspend")):
        done = len(parse_index_ranges(status.get("completedIndexes")))
        return "Queued" if done == 0 else "Paused"
    return "Running"


def _counts(job: dict) -> dict:
    status = job.get("status") or {}
    spec = job.get("spec") or {}
    return {
        "total": spec.get("completions") or 0,
        "active": status.get("active") or 0,
        "done": len(parse_index_ranges(status.get("completedIndexes"))),
        "failed": len(parse_index_ranges(status.get("failedIndexes"))),
    }


def _results_base(namespace: str, pipeline: str, cfg) -> str:
    return f"{cfg.public_results_base}/{namespace}/{pipeline}"


def summarize(job: dict, cfg, warmup: dict) -> dict:
    """``JobSummary``: one row for ``GET /api/v1/jobs``. ``warmup`` is the
    caller's pre-matched ``{phase, reason?}`` (Task 28)."""
    meta = job.get("metadata") or {}
    namespace = meta.get("namespace", "")
    pipeline = _labels(job).get(_PIPELINE_LABEL, "")
    return {
        "namespace": namespace,
        "name": meta.get("name", ""),
        "pipeline": pipeline,
        "phase": _phase(job),
        "counts": _counts(job),
        "suspended": bool((job.get("spec") or {}).get("suspend")),
        "createdAt": meta.get("creationTimestamp"),
        "resultsBase": _results_base(namespace, pipeline, cfg),
        "warmup": warmup,
    }


def match_warmup(job: dict, warmup_jobs: list[dict]) -> dict | None:
    """This campaign Job's warm-up Job, by namespace + pipeline label."""
    ns = (job.get("metadata") or {}).get("namespace")
    pipeline = _labels(job).get(_PIPELINE_LABEL)
    for w in warmup_jobs:
        wm = w.get("metadata") or {}
        if wm.get("namespace") == ns and _labels(w).get(_PIPELINE_LABEL) == pipeline:
            return w
    return None


def warmup_phase(job: dict) -> str:
    """pending/running/succeeded/failed for one warm-up Job."""
    status = job.get("status") or {}
    conditions = status.get("conditions") or []
    if any(
        c.get("type") == "Complete" and c.get("status") == "True" for c in conditions
    ):
        return "succeeded"
    if any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions):
        return "failed"
    return "running" if (status.get("active") or 0) > 0 else "pending"


def warmup_reason(pods: list[dict] | None) -> dict | None:
    """No warm-up log exists (Task 28) -- this is the only way to explain."""
    return _wrapper_reason(_newest(pods), "warmup") if pods else None


def _volume_lines(configmap: dict | None) -> list[str]:
    data = (configmap or {}).get("data") or {}
    return [line for line in data.get("volumes.txt", "").splitlines() if line]


def _source_url(line: str) -> str | None:
    """The URL half of a ``volumes.txt`` line (``<id>\t<manifest url>``) — what
    the card's "source" link opens in the viewer before there are results.
    An ``images:`` line lists bare image URLs instead of a manifest, so it has
    no source to open (converter ``models.Volume.source_line``)."""
    source = line.partition("\t")[2]
    return source if source.startswith(("http://", "https://")) else None


def _wrapper_reason(pod: dict, container: str = "wrapper") -> dict | None:
    """``container``'s termination message (``state.terminated``, else
    ``lastState.terminated`` after a restart, D6/task-3), parsed once into
    this API's structured ``reason`` rather than a raw JSON blob. Defaults
    to the campaign wrapper; a warm-up Job's is ``warmup`` (Task 28)."""
    status = pod.get("status") or {}
    for cs in status.get("containerStatuses") or []:
        if cs.get("name") != container:
            continue
        term = (cs.get("state") or {}).get("terminated")
        if term is None:
            term = (cs.get("lastState") or {}).get("terminated")
        if term is not None and (message := term.get("message")) is not None:
            return _name_the_deadline(_reason(message), status.get("reason"))
    return None


def _reason(message: str) -> dict:
    """``{"stage", "permanent", "error"}`` from the wrapper's termination log.

    Anything that is not that object -- an older wrapper, a kubelet
    ``FallbackToLogsOnError`` tail, a truncated write -- becomes the raw text
    in ``error`` with the other two fields ``null``, so the shape a client
    parses never depends on which wrapper wrote the pod.
    """
    try:
        doc = json.loads(message)
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("error"), str):
        return {"stage": None, "permanent": None, "error": message}
    stage, permanent = doc.get("stage"), doc.get("permanent")
    return {
        "stage": stage if isinstance(stage, str) else None,
        "permanent": permanent if isinstance(permanent, bool) else None,
        "error": doc["error"],
    }


def _name_the_deadline(reason: dict, pod_reason: str | None) -> dict:
    """A pod that overran its ``activeDeadlineSeconds`` is SIGTERMed exactly
    like a drained one, and the wrapper cannot tell them apart -- it writes
    ``"error": "SIGTERM"`` either way. The pod's own ``status.reason`` can, so
    swap that one field for ``DeadlineExceeded``: the card then says "budget
    exceeded", not "node drained"."""
    if pod_reason != "DeadlineExceeded" or reason.get("error") != "SIGTERM":
        return reason
    return {**reason, "error": "DeadlineExceeded"}


def _pod_completion_index(pod: dict) -> int | None:
    raw = _labels(pod).get(_INDEX_LABEL)
    return int(raw) if raw is not None else None


def _pods_by_index(pods: list[dict] | None) -> dict[int, list[dict]]:
    by_index: dict[int, list[dict]] = {}
    for pod in pods or []:
        idx = _pod_completion_index(pod)
        if idx is not None:
            by_index.setdefault(idx, []).append(pod)
    return by_index


def _newest(pods: list[dict]) -> dict:
    return max(
        pods, key=lambda p: (p.get("metadata") or {}).get("creationTimestamp", "")
    )


def _volume_state(
    idx: int, completed: set[int], failed: set[int], has_pod: bool
) -> str:
    if idx in completed:
        return "done"
    if idx in failed:
        return "failed"
    return "active" if has_pod else "pending"


def _log_url(pipeline: str, volume_id: str, cfg) -> str:
    """Absolute URL at a bucket-root key, no namespace/S3_PREFIX prefix —
    matches ``ResultStore.run_log_key()``
    (packages/wrapper/src/htrflow_batch/store.py), which writes the run log
    outside ``volume_prefix`` on purpose: the ``status/`` tree is shared
    across namespaces, unlike the per-namespace results under
    ``resultsBase``. Absolute (not a bare key) because the browser has no
    bucket base URL to resolve a key against."""
    return f"{cfg.public_results_base}/status/logs/{pipeline}/{volume_id}.txt"


def _pipeline_yaml(configmap: dict | None) -> str:
    return ((configmap or {}).get("data") or {}).get("pipeline.yaml", "")


def _pipeline_steps(text: str) -> list[str]:
    """The ``step`` name of each entry under ``steps:``, in order — what the
    card's pipeline chip lists in its tooltip. A ConfigMap that is missing,
    empty or shaped differently is no steps rather than an error: the chip
    then just names the pipeline, and the campaign is unaffected either way."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    steps = doc.get("steps") if isinstance(doc, dict) else None
    if not isinstance(steps, list):
        return []
    # Strings only: the schema says `step: <name>` but a hand-edited
    # ConfigMap can put anything there, and the field is typed `string[]` all
    # the way to the chip's tooltip.
    return [
        s["step"]
        for s in steps
        if isinstance(s, dict) and isinstance(s.get("step"), str)
    ]


def _latest(volumes: list[dict]) -> dict | None:
    """The volume a folded card shows: the newest ``active`` one by index,
    else the newest ``done`` one, else nothing. Computed here over EVERY
    volume rather than in the browser over the loaded page, because a
    campaign of thousands shows its first 200 rows and the volume in flight
    is almost never among them."""
    for state in ("active", "done"):
        matching = [v for v in volumes if v["state"] == state]
        if matching:
            return max(matching, key=lambda v: v["index"])
    return None


def detail(
    job: dict,
    configmap: dict | None,
    pods: list[dict] | None,
    cfg,
    offset: int = 0,
    limit: int = 200,
    pipeline_configmap: dict | None = None,
    *,
    warmup: dict,
) -> dict:
    """``JobDetail``: ``JobSummary`` plus per-index rows and top failures for
    ``GET /api/v1/jobs/{ns}/{name}``, paged by index. ``warmup`` passes
    through to ``summarize`` unchanged (Task 28)."""
    summary = summarize(job, cfg, warmup)
    status = job.get("status") or {}
    completed = parse_index_ranges(status.get("completedIndexes"))
    failed = parse_index_ranges(status.get("failedIndexes"))
    pods_by_index = _pods_by_index(pods)
    results_base = summary["resultsBase"]
    pipeline = summary["pipeline"]

    # Annotated because the rows are heterogeneous (int index, str URLs,
    # nullable sourceUrl) and the sort below needs a comparable key type.
    volumes: list[dict] = []
    for idx, line in enumerate(_volume_lines(configmap)):
        vol_id = line.split("\t", 1)[0]
        row = {
            "index": idx,
            "id": vol_id,
            "state": _volume_state(idx, completed, failed, idx in pods_by_index),
            "manifestUrl": f"{results_base}/{vol_id}/manifest.json",
            "iiifUrl": f"{results_base}/{vol_id}/iiif.json",
            "altoPrefix": f"{results_base}/{vol_id}/alto/",
            "logUrl": _log_url(pipeline, vol_id, cfg),
            "sourceUrl": _source_url(line),
        }
        if idx in pods_by_index:
            newest = _newest(pods_by_index[idx])
            reason = _wrapper_reason(newest)
            if reason is not None:
                row["reason"] = reason
        volumes.append(row)

    failures = sorted(
        (v for v in volumes if v["state"] == "failed" and "reason" in v),
        key=lambda v: v["index"],
        reverse=True,
    )[:_MAX_FAILURES]

    pipeline_yaml = _pipeline_yaml(pipeline_configmap)
    return {
        **summary,
        # Detail only, never the list: one pipeline YAML per campaign row
        # would be most of the list response's bytes for a chip nobody has
        # clicked yet.
        "pipelineSteps": _pipeline_steps(pipeline_yaml),
        "pipelineYaml": pipeline_yaml,
        # Like `failures`, computed over every volume and unaffected by
        # offset/limit.
        "latest": _latest(volumes),
        "failures": failures,
        "volumes": volumes[offset : offset + limit],
    }
