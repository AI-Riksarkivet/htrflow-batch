"""Pure projections of Kubernetes API dicts onto the ``/api/v1/jobs`` shapes.

Every function here takes plain dicts — what ``kube.Reader`` reads back off
the API server, or what a test builds by hand — and never touches the
cluster or the network. That is what makes these testable without a fixture
cluster (docs: task-4-brief).
"""

from __future__ import annotations

import json

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


def configmap_ref(job: dict) -> str | None:
    """Name of the ConfigMap mounted as the Job's ``campaign`` volume."""
    pod_spec = ((job.get("spec") or {}).get("template") or {}).get("spec") or {}
    for v in pod_spec.get("volumes") or []:
        if v.get("name") == "campaign":
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
        return "Failed"
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


def summarize(job: dict, cfg) -> dict:
    """``JobSummary``: one row per campaign Job for ``GET /api/v1/jobs``."""
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
    }


def _volume_lines(configmap: dict | None) -> list[str]:
    data = (configmap or {}).get("data") or {}
    return [line for line in data.get("volumes.txt", "").splitlines() if line]


def _wrapper_message(pod: dict) -> str | None:
    """The wrapper's termination message: ``state.terminated`` if the
    container is currently terminated, else ``lastState.terminated`` after a
    restart (D6/task-3)."""
    status = pod.get("status") or {}
    for cs in status.get("containerStatuses") or []:
        if cs.get("name") != "wrapper":
            continue
        term = (cs.get("state") or {}).get("terminated")
        if term is None:
            term = (cs.get("lastState") or {}).get("terminated")
        if term is not None:
            return _name_the_deadline(term.get("message"), status.get("reason"))
    return None


def _name_the_deadline(message: str | None, pod_reason: str | None) -> str | None:
    """A pod that overran its ``activeDeadlineSeconds`` is SIGTERMed exactly
    like a drained one, and the wrapper cannot tell them apart -- it writes
    ``"error": "SIGTERM"`` either way. The pod's own ``status.reason`` can, so
    swap that one field for ``DeadlineExceeded``: the card then says "budget
    exceeded", not "node drained". The message stays the same JSON object
    (``stage``/``permanent``/``error``) every other reader already parses."""
    if pod_reason != "DeadlineExceeded" or not message:
        return message
    try:
        reason = json.loads(message)
    except ValueError:
        return message  # FallbackToLogsOnError, or an older wrapper
    if not isinstance(reason, dict) or reason.get("error") != "SIGTERM":
        return message
    return json.dumps({**reason, "error": "DeadlineExceeded"})


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


def detail(
    job: dict,
    configmap: dict | None,
    pods: list[dict] | None,
    cfg,
    offset: int = 0,
    limit: int = 200,
) -> dict:
    """``JobDetail``: ``JobSummary`` plus per-index rows and top failures for
    ``GET /api/v1/jobs/{ns}/{name}``, paged by index."""
    summary = summarize(job, cfg)
    status = job.get("status") or {}
    completed = parse_index_ranges(status.get("completedIndexes"))
    failed = parse_index_ranges(status.get("failedIndexes"))
    pods_by_index = _pods_by_index(pods)
    results_base = summary["resultsBase"]
    pipeline = summary["pipeline"]

    volumes = []
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
        }
        if idx in pods_by_index:
            newest = _newest(pods_by_index[idx])
            message = _wrapper_message(newest)
            if message is not None:
                row["reason"] = message
        volumes.append(row)

    failures = sorted(
        (v for v in volumes if v["state"] == "failed" and "reason" in v),
        key=lambda v: v["index"],
        reverse=True,
    )[:_MAX_FAILURES]

    return {
        **summary,
        "failures": failures,
        "volumes": volumes[offset : offset + limit],
    }
