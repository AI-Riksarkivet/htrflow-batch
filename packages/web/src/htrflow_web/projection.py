"""Pure projections of Kubernetes API dicts onto the ``/api/v1/jobs`` shapes.

Every function here takes plain dicts — what ``kube.Reader`` reads back off
the API server, or what a test builds by hand — and never touches the
cluster or the network. That is what makes these testable without a fixture
cluster (docs: task-4-brief).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import yaml

_PIPELINE_LABEL = "htrflow.riksarkivet.se/pipeline"
_CAMPAIGN_LABEL = "htrflow.riksarkivet.se/campaign"
_MANAGED_BY_LABEL = "htrflow.riksarkivet.se/managed-by"
#: Marks the ConfigMap this module writes apart from the campaign ConfigMap
#: the converter renders. Both carry `managed-by=converter`, which is what
#: `htrflow-campaigns apply --prune` deletes a cancelled campaign by, so the
#: record and its status go together when the campaign file leaves git.
KIND_LABEL = "htrflow.riksarkivet.se/kind"
STATUS_KIND = "status"
#: ``campaign-<name>`` + this is the status ConfigMap of that campaign --
#: the one name both the read API and `apply` build (B76).
STATUS_SUFFIX = "-status"
_INDEX_LABEL = "batch.kubernetes.io/job-completion-index"
_MAX_FAILURES = 50
#: A campaign in one of these is over: nothing more will be written about
#: it, and `apply` leaves it alone rather than recreating a reaped Job.
FINISHED_PHASES = ("Succeeded", "Failed", "PartiallyFailed")


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


def _internal_results_base(namespace: str, pipeline: str, cfg) -> str:
    """Where THIS POD reaches the results bucket -- ``cfg.internal_results_base``
    when set, else the same public one (real AWS, and every lightweight `cfg`
    double in this package's own tests, which carry no such field at all).
    Used only for ``ProgressReader.fetch`` below: every URL a browser follows
    (manifestUrl, iiifUrl, logUrl, ``resultsBase`` itself) stays built from
    ``_results_base``, the public one."""
    base = getattr(cfg, "internal_results_base", "") or cfg.public_results_base
    return f"{base}/{namespace}/{pipeline}"


def _finished_at(job: dict) -> str | None:
    """When the campaign stopped. ``completionTime`` for a Job that
    succeeded; a Job that FAILED has none at all, and its condition's
    transition is then the only clock there is."""
    status = job.get("status") or {}
    if status.get("completionTime"):
        return status["completionTime"]
    for c in status.get("conditions") or []:
        if c.get("type") in ("Complete", "Failed") and c.get("status") == "True":
            return c.get("lastTransitionTime")
    return None


def summarize(job: dict, cfg, warmup: dict) -> dict:
    """``JobSummary``: one row for ``GET /api/v1/jobs``. ``warmup`` is the
    caller's pre-matched ``{phase, reason?}`` (Task 28)."""
    meta = job.get("metadata") or {}
    namespace = meta.get("namespace", "")
    pipeline = _labels(job).get(_PIPELINE_LABEL, "")
    return {
        "namespace": namespace,
        "name": meta.get("name", ""),
        # The campaign FILE's name. Not the same as `name` for a campaign
        # split into parts (`kyrk` vs `kyrk-part1`), and it is the file that
        # leaving git prunes the record.
        "campaign": _labels(job).get(_CAMPAIGN_LABEL, "") or meta.get("name", ""),
        "pipeline": pipeline,
        "phase": _phase(job),
        "counts": _counts(job),
        "suspended": bool((job.get("spec") or {}).get("suspend")),
        "createdAt": meta.get("creationTimestamp"),
        # The dates the record keeps: past the Job's TTL these are all the
        # campaign page has left to say when it ran (B76).
        "startedAt": (job.get("status") or {}).get("startTime"),
        "finishedAt": _finished_at(job),
        "resultsBase": _results_base(namespace, pipeline, cfg),
        "warmup": warmup,
        # There is a Job behind this row. The record's rows say False (B76).
        "jobGone": False,
    }


def status_record(row: dict, failures: list[dict] | None = None) -> dict[str, str]:
    """The status ConfigMap's ``data``, from a summary this request already
    computed. The field names are a contract: `htrflow-campaigns apply`
    parses them back to decide whether a finished campaign whose Job has
    been reaped should be left alone (B76).

    ``failures`` is passed only by the detail endpoint, which is the one
    that reads pods -- a list response has no per-volume reasons, and
    writing an empty ``failedVolumes`` there would wipe what the detail
    endpoint observed, so the field is absent instead of empty.
    """
    counts = row["counts"]
    data = {
        "phase": row["phase"],
        "volumesTotal": str(counts["total"]),
        "volumesDone": str(counts["done"]),
        "volumesFailed": str(counts["failed"]),
        "startedAt": row["startedAt"] or "",
        "finishedAt": row["finishedAt"] or "",
        "resultsBase": row["resultsBase"],
    }
    if failures is not None:
        data["failedVolumes"] = _failed_volumes(failures)
    return data


#: A reason is one sentence for a card to show. The wrapper writes whatever
#: its termination message said, which can be a whole Python traceback.
MAX_REASON = 300
#: And the field as a whole, serialized. A ConfigMap is capped at 1 MiB by
#: the API server, and a record it refuses is a campaign with no record at
#: all (2026-09-14 audit) -- so the oldest failures are dropped until it
#: fits, rather than the write failing.
MAX_FAILED_VOLUMES = 200 * 1024


def _failed_volumes(failures: list[dict]) -> str:
    entries = [
        {
            "id": v["id"][:MAX_REASON],
            "reason": ((v.get("reason") or {}).get("error") or "")[:MAX_REASON],
        }
        for v in failures[:_MAX_FAILURES]
    ]
    while entries:
        blob = json.dumps(entries, separators=(",", ":"))
        if len(blob.encode()) <= MAX_FAILED_VOLUMES:
            return blob
        entries.pop()
    return "[]"


#: Phases a stored record may carry -- the ones this API itself writes. A
#: record saying anything else is not a campaign the page can draw, and is
#: left out rather than guessed at.
_PHASES = (*FINISHED_PHASES, "Running", "Queued", "Paused")
#: What a reaped campaign's row says when the last thing anyone observed was
#: a campaign still going. The Job is gone, so nothing is running; how it
#: ended is simply not on record -- `apply` writes the terminal record from
#: the live Job, so this is what a Job deleted by hand, or by a prune, or
#: reaped between the last apply and the last page view, leaves behind.
#: Reporting `Running` for ever is the one answer that is certainly wrong.
UNKNOWN_PHASE = "Unknown"


def _int(text: object) -> int:
    try:
        return int(str(text))
    except ValueError:
        return 0


def record_summary(record: dict, status: dict, cfg, warmup: dict) -> dict | None:
    """One row for a campaign whose Job is gone -- reaped by its
    ``ttlSecondsAfterFinished`` -- from the two ConfigMaps it left behind
    (B76). The same shape ``summarize`` returns, so the page draws it with
    no special case beyond the ``jobGone`` chip.

    A campaign ConfigMap with no status ConfigMap beside it is NOT one of
    these and has no row here: this API never saw that campaign run (it is
    being applied right now, say), and the Job, when it appears, says more
    than a guess would.
    """
    meta = record.get("metadata") or {}
    data = status.get("data") or {}
    if data.get("phase") not in _PHASES:
        return None
    labels = _labels(record)
    namespace = meta.get("namespace", "")
    name = meta.get("name", "").removeprefix("campaign-")
    pipeline = labels.get(_PIPELINE_LABEL, "")
    return {
        "namespace": namespace,
        "name": name,
        "campaign": labels.get(_CAMPAIGN_LABEL, "") or name,
        "pipeline": pipeline,
        "phase": data["phase"] if data["phase"] in FINISHED_PHASES else UNKNOWN_PHASE,
        "counts": {
            "total": _int(data.get("volumesTotal")),
            "active": 0,  # nothing is running: there is no Job to run it
            "done": _int(data.get("volumesDone")),
            "failed": _int(data.get("volumesFailed")),
        },
        "suspended": False,
        "createdAt": meta.get("creationTimestamp"),
        "startedAt": data.get("startedAt") or None,
        "finishedAt": data.get("finishedAt") or None,
        # Derived, never the stored one. The record keeps the base the
        # campaign ran under and that is worth keeping, but it is
        # informational: the links on this row and the progress files THIS
        # process fetches for them have to name the same place, and the
        # progress base was always derived (2026-09-14 review).
        "resultsBase": _results_base(namespace, pipeline, cfg),
        "warmup": warmup,
        "jobGone": True,
    }


def _recorded_reasons(status: dict) -> dict[str, str]:
    """``{volume id: reason}`` from the status record's ``failedVolumes``.
    Those sentences are the detail endpoint's, observed while the pods still
    existed; anything finer is in the volume's own ``manifest.json`` in the
    bucket (docs: reference/s3-layout)."""
    try:
        listed = json.loads((status.get("data") or {}).get("failedVolumes") or "[]")
    except ValueError:
        return {}
    if not isinstance(listed, list):
        return {}
    return {
        entry["id"]: str(entry.get("reason", ""))
        for entry in listed
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


#: What a volume nobody named as failed did, given how the campaign ended.
#: A reaped campaign has no per-index state left -- that was the Job's
#: ``completedIndexes`` -- so the campaign's own ending is what every row
#: that is not in ``failedVolumes`` gets. ``Unknown`` is the record's own
#: word for "nobody wrote down how this ended", and its rows say the same:
#: calling them `done` claimed the opposite, and contradicted the record's
#: own ``volumesFailed`` whenever the detail endpoint never got to name the
#: failures (2026-09-14 review). The bucket still answers for such a row --
#: its progress file, or its manifest.json, says what actually happened.
UNKNOWN_STATE = "unknown"
_RECORD_STATE = {
    "Succeeded": "done",
    "PartiallyFailed": "done",
    UNKNOWN_PHASE: UNKNOWN_STATE,
    "Failed": "failed",
}


def record_detail(
    row: dict,
    record: dict | None,
    status: dict,
    cfg,
    pipeline_configmap: dict | None,
    offset: int = 0,
    limit: int = 200,
    fetch_progress=None,
) -> dict:
    """``JobDetail`` for a campaign whose Job is gone.

    The Job's ``completedIndexes`` went with the Job, but manifest.json,
    iiif.json, alto/ and the run log are all still in the bucket -- and this
    endpoint answered with no rows at all, so a finished campaign was one
    nobody could open a single volume of (R1, the product owner,
    2026-09-14). The rows are rebuilt from what has no TTL: the campaign
    record's ``volumes.txt`` for the ids and their sources, the status
    record's ``failedVolumes`` for the ones that failed and why, and the
    campaign's own ending for everything else. Their page counts come from
    the bucket like a live campaign's, through the same cap and deadline.
    """
    pipeline_yaml = _pipeline_yaml(pipeline_configmap)
    reasons = _recorded_reasons(status)
    results_base, pipeline = row["resultsBase"], row["pipeline"]
    ending = _RECORD_STATE.get(row["phase"], UNKNOWN_STATE)
    volumes: list[dict] = []
    for idx, line in enumerate(_volume_lines(record)):
        vol_id = line.split("\t", 1)[0]
        state = "failed" if vol_id in reasons else ending
        volume = _volume_row(idx, line, state, results_base, pipeline, cfg)
        if vol_id in reasons:
            volume["reason"] = {
                "stage": None,
                "permanent": None,
                "error": reasons[vol_id],
            }
        volumes.append(volume)

    failures = sorted(
        (v for v in volumes if v["state"] == "failed" and "reason" in v),
        key=lambda v: v["index"],
        reverse=True,
    )[:_MAX_FAILURES]
    page = volumes[offset : offset + limit]
    latest = _latest(volumes)
    known = _attach_progress(
        [*page, *failures, *([latest] if latest else [])],
        _internal_results_base(row["namespace"], pipeline, cfg),
        fetch_progress or (lambda *_args: None),
    )
    return {
        **row,
        **_campaign_pages(known),
        "pipelineSteps": _pipeline_steps(pipeline_yaml),
        "pipelineYaml": pipeline_yaml,
        "latest": latest,
        "failures": failures,
        "volumes": page,
    }


#: Values that say nothing. `"[]"` is `failedVolumes`' own empty.
_SAYS_NOTHING = ("", "[]")


def merge_record(stored: dict[str, str], fresh: dict[str, str]) -> dict[str, str]:
    """The stored record updated with a fresh observation, never shrunk.

    A campaign's pods are garbage-collected long before its record is, so a
    detail request an hour after the failures happened sees no reasons at
    all -- and this record is the only place those sentences survive. Two
    rules, both about not losing ground: a value that says nothing never
    replaces one that says something, and ``finishedAt`` never moves
    backwards (the read API and `htrflow-campaigns apply` both write it, and
    they do not see the same clock).
    """
    merged = dict(stored)
    for key, value in fresh.items():
        old = stored.get(key, "")
        if value in _SAYS_NOTHING and old not in _SAYS_NOTHING:
            continue
        if key == "finishedAt" and not _is_later(value, old):
            continue
        merged[key] = value
    return merged


def _instant(text: str) -> datetime | None:
    """An RFC 3339 timestamp as a moment in time. A value without an offset
    is read as UTC -- which is what every writer of this field means."""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_later(value: str, old: str) -> bool:
    """Whether ``value`` is a later moment than ``old``. Compared as moments,
    not as text: the two writers of this field do not see the same clock and
    need not write the same offset, and `09:00Z` sorts before `10:00+02:00`
    as a string while being an hour after it (2026-09-14 audit). A fresh
    value that is not a timestamp at all never replaces one that is."""
    fresh, stored = _instant(value), _instant(old)
    if fresh is None:
        return False
    return stored is None or fresh >= stored


def status_configmap(row: dict, data: dict[str, str]) -> dict:
    """The whole object the read API applies. Named after the campaign
    ConfigMap beside it and labelled like it, so a prune takes both."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"campaign-{row['name']}{STATUS_SUFFIX}",
            "namespace": row["namespace"],
            "labels": {
                _MANAGED_BY_LABEL: "converter",
                _CAMPAIGN_LABEL: row["campaign"],
                _PIPELINE_LABEL: row["pipeline"],
                KIND_LABEL: STATUS_KIND,
            },
        },
        "data": data,
    }


def match_warmup(job: dict, warmup_jobs: list[dict]) -> dict | None:
    """This campaign Job's warm-up Job, by namespace + pipeline label. A job
    without a pipeline label never matches -- else it could pair up with a
    warm-up Job that also lacks one, on ``None == None``."""
    ns = (job.get("metadata") or {}).get("namespace")
    pipeline = _labels(job).get(_PIPELINE_LABEL, "")
    if not pipeline:
        return None
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


def _terminated_message(statuses: list[dict], name: str | None) -> str | None:
    """The termination message left by ``name``'s container
    (``state.terminated``, else ``lastState.terminated`` after a restart,
    D6/task-3). ``name`` of ``None`` takes any container that terminated
    NON-ZERO instead -- how the init containers are read, where the name is
    not known in advance and a successful one explains nothing."""
    for cs in statuses:
        if name is not None and cs.get("name") != name:
            continue
        term = (cs.get("state") or {}).get("terminated") or (
            cs.get("lastState") or {}
        ).get("terminated")
        if term is None or (name is None and not term.get("exitCode")):
            continue
        if (message := term.get("message")) is not None:
            return message
    return None


def wrapper_reason(pod: dict, container: str = "wrapper") -> dict | None:
    """``container``'s termination message, parsed once into this API's
    structured ``reason`` rather than a raw JSON blob. Defaults to the
    campaign wrapper; ``app.py`` passes ``"warmup"`` for a warm-up Job's pod
    (Task 28) -- there is no warm-up log to read instead.

    A pod can fail before ``container`` ever starts, and then an init
    container's message is all it has to say -- `warmup-wait` giving up on
    the warm-up marker and exiting 13 is exactly that, and without this
    fall-through the index is `failed` with no ``reason`` at all, the silence
    the bounded gate exists to break (B74). That message is stderr
    (`terminationMessagePolicy: FallbackToLogsOnError`), a sentence rather
    than JSON, which ``_reason`` already carries through as ``error``."""
    status = pod.get("status") or {}
    message = _terminated_message(status.get("containerStatuses") or [], container)
    if message is None:
        message = _terminated_message(status.get("initContainerStatuses") or [], None)
    if message is None:
        return None
    return _name_the_deadline(_reason(message), status.get("reason"))


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
    """The pod's index, or ``None`` when it has no readable one. The label is
    the Job controller's, but a hand-made pod can carry anything, and one
    such pod used to take the whole campaign page down (2026-09-14 audit)."""
    raw = _labels(pod).get(_INDEX_LABEL)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _pods_by_index(pods: list[dict] | None) -> dict[int, list[dict]]:
    by_index: dict[int, list[dict]] = {}
    for pod in pods or []:
        idx = _pod_completion_index(pod)
        if idx is not None:
            by_index.setdefault(idx, []).append(pod)
    return by_index


def newest(pods: list[dict]) -> dict:
    """The most recently created of a set of pods (a completion index's
    retries, or a warm-up Job's, Task 28) -- ``pods`` must be non-empty."""
    return max(
        pods, key=lambda p: (p.get("metadata") or {}).get("creationTimestamp", "")
    )


def _volume_row(
    idx: int, line: str, state: str, results_base: str, pipeline: str, cfg
) -> dict:
    """One row of a campaign's volume table, from its ``volumes.txt`` line.
    Every URL below is derived from the id, so the same function builds a
    live campaign's rows and a reaped one's -- there is nothing to read off
    the Job (R1, 2026-09-14)."""
    vol_id = line.split("\t", 1)[0]
    return {
        "index": idx,
        "id": vol_id,
        "state": state,
        "manifestUrl": f"{results_base}/{vol_id}/manifest.json",
        "iiifUrl": f"{results_base}/{vol_id}/iiif.json",
        "altoPrefix": f"{results_base}/{vol_id}/alto/",
        "logUrl": _log_url(pipeline, vol_id, cfg),
        "sourceUrl": _source_url(line),
    }


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


#: Cost cap (finding 5): ProgressReader.fetch is one sequential GET, and a
#: request's rows can otherwise run to `limit` (up to 1000) plus `failures`
#: (up to 50) plus `latest`. Rows still running are asked first -- theirs is
#: the progress actually changing -- and the rest (mostly done volumes,
#: whose progress.json never changes again) fill whatever budget is left.
PROGRESS_FETCH_CAP = 32

#: Seconds the whole fan-out may take, cap or no cap. Each fetch is one
#: sequential GET with progress.py's own short timeout, so an unreachable
#: bucket cost cap x that timeout -- over a minute of one worker, inside a
#: sync handler, and forty such requests emptied the threadpool /healthz is
#: answered from (2026-09-14 audit). Past the deadline the remaining rows
#: are answered with no progress, which is what an unreadable file already
#: means: a decoration missing from a page that still draws.
PROGRESS_FETCH_BUDGET = 5.0


def _attach_progress(rows: list[dict], results_base: str, fetch) -> list[dict]:
    """Give every row the response actually carries its ``progress`` — the
    page of volumes, plus ``latest`` and the failures, which are returned
    from outside that page. Passed in rather than read here (progress.py does
    the HTTP) so this module stays pure and testable without a bucket. One
    row is fetched once even when it appears in two of the three lists, and
    at most PROGRESS_FETCH_CAP rows are ever fetched at all (docs:
    s3-layout), so a `limit=1000` request cannot turn into a thousand
    sequential GETs -- ``pending`` rows cost nothing (fetch short-circuits
    them) and are not worth budget either way."""
    shown = list({id(row): row for row in rows}.values())
    fetchable = [row for row in shown if row["state"] != "pending"]
    running = [row for row in fetchable if row["state"] == "active"]
    rest = [row for row in fetchable if row["state"] != "active"]
    asked = {id(row) for row in (running + rest)[:PROGRESS_FETCH_CAP]}
    deadline = time.monotonic() + PROGRESS_FETCH_BUDGET
    for row in shown:
        wanted = id(row) in asked and time.monotonic() < deadline
        row["progress"] = (
            fetch(results_base, row["id"], row["state"]) if wanted else None
        )
    return [row for row in shown if row["progress"]]


def _campaign_pages(rows: list[dict]) -> dict:
    """What the card says above its table, summed over the volumes THIS
    response covered -- all the campaign can know without one GET per volume
    in the archive. ``lastError`` is the most recent page failure among them,
    carrying the volume it happened in and that volume's run log: the row it
    came from is often outside the page the reader is looking at."""
    known = [row["progress"] for row in rows]
    last_errors = [
        (
            p["updatedAt"] or "",
            {**p["lastError"], "volume": row["id"], "logUrl": row["logUrl"]},
        )
        for row in rows
        if (p := row["progress"])["lastError"]
    ]
    return {
        "pagesDone": sum(p["done"] for p in known),
        "pagesTotal": sum(p["total"] for p in known),
        "pagesFailed": sum(p["failed"] for p in known),
        "errors": sum(p["errors"] for p in known),
        "lastError": max(last_errors, key=lambda e: e[0])[1] if last_errors else None,
    }


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
    fetch_progress=None,
) -> dict:
    """``JobDetail``: ``JobSummary`` plus per-index rows and top failures for
    ``GET /api/v1/jobs/{ns}/{name}``, paged by index. ``warmup`` passes
    through to ``summarize`` unchanged (Task 28); ``fetch_progress`` is
    ``(results_base, volume id, state) -> progress | None``
    (``app.py`` wires ``progress.ProgressReader.fetch``) -- called with the
    INTERNAL results base (this pod's own way to the bucket), never the
    public one every URL below is built from: on the PoC they are not the
    same address (docs: development/local-k3s)."""
    summary = summarize(job, cfg, warmup)
    status = job.get("status") or {}
    completed = parse_index_ranges(status.get("completedIndexes"))
    failed = parse_index_ranges(status.get("failedIndexes"))
    pods_by_index = _pods_by_index(pods)
    results_base = summary["resultsBase"]
    pipeline = summary["pipeline"]
    internal_base = _internal_results_base(summary["namespace"], pipeline, cfg)

    # Annotated because the rows are heterogeneous (int index, str URLs,
    # nullable sourceUrl) and the sort below needs a comparable key type.
    volumes: list[dict] = []
    for idx, line in enumerate(_volume_lines(configmap)):
        state = _volume_state(idx, completed, failed, idx in pods_by_index)
        row = _volume_row(idx, line, state, results_base, pipeline, cfg)
        if idx in pods_by_index:
            newest_pod = newest(pods_by_index[idx])
            reason = wrapper_reason(newest_pod)
            if reason is not None:
                row["reason"] = reason
        volumes.append(row)

    failures = sorted(
        (v for v in volumes if v["state"] == "failed" and "reason" in v),
        key=lambda v: v["index"],
        reverse=True,
    )[:_MAX_FAILURES]

    page = volumes[offset : offset + limit]
    known = _attach_progress(
        [*page, *failures, *([latest] if (latest := _latest(volumes)) else [])],
        internal_base,
        fetch_progress or (lambda *_args: None),
    )

    pipeline_yaml = _pipeline_yaml(pipeline_configmap)
    return {
        **summary,
        **_campaign_pages(known),
        # Detail only, never the list: one pipeline YAML per campaign row
        # would be most of the list response's bytes for a chip nobody has
        # clicked yet.
        "pipelineSteps": _pipeline_steps(pipeline_yaml),
        "pipelineYaml": pipeline_yaml,
        # Like `failures`, computed over every volume and unaffected by
        # offset/limit.
        "latest": latest,
        "failures": failures,
        "volumes": page,
    }
