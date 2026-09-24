"""Pure projections of Kubernetes API dicts onto the ``/api/v1/jobs`` shapes.

Every function here takes plain dicts — what ``kube.Reader`` reads back off
the API server, or what a test builds by hand — and never touches the
cluster or the network. That is what makes these testable without a fixture
cluster (docs: task-4-brief).
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import quote, urlsplit

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


def pipeline_of(obj: dict) -> str:
    """The pipeline id an object is labelled with: a campaign's Job, its
    record, or a pipeline ConfigMap itself."""
    return _labels(obj).get(_PIPELINE_LABEL, "")


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


def summarize(job: dict, cfg, warmup: dict, quality_prediction: bool = False) -> dict:
    """``JobSummary``: one row for ``GET /api/v1/jobs``. ``warmup`` is the
    caller's pre-matched ``{phase, reason?}`` (Task 28), and
    ``quality_prediction`` whether the campaign's pipeline scores page
    quality (``quality_prediction`` below, on its pipeline ConfigMap)."""
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
        # On the list row, not only the detail: the card holds its quality
        # column (and the line under the totals) from its first paint, so
        # nothing moves when the detail lands.
        "qualityPrediction": quality_prediction,
    }


def status_record(
    row: dict, failures: list[dict] | None = None, *, job_uid: str = ""
) -> dict[str, str]:
    """The status ConfigMap's ``data``, from a summary this request already
    computed. The field names are a contract: `htrflow-campaigns apply`
    parses them back to decide whether a finished campaign whose Job has
    been reaped should be left alone (B76). ``job_uid`` is the Job's
    ``metadata.uid``: which run the record is about (3075).

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
        "jobUid": job_uid,
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
    return _capped(
        [
            {
                "id": v["id"][:MAX_REASON],
                "reason": ((v.get("reason") or {}).get("error") or "")[:MAX_REASON],
            }
            for v in failures
        ]
    )


def _capped(entries: list[dict]) -> str:
    entries = entries[:_MAX_FAILURES]
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


def record_summary(
    record: dict, status: dict, cfg, warmup: dict, quality_prediction: bool = False
) -> dict | None:
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
        "qualityPrediction": quality_prediction,
    }


def _recorded_reasons(status: dict) -> dict[str, str]:
    """``{volume id: reason}`` from the status record's ``failedVolumes``.
    Those sentences are the detail endpoint's, observed while the pods still
    existed; anything finer is in the volume's own ``manifest.json`` in the
    bucket (docs: reference/s3-layout). None when they are another run's
    than the record's (``_FAILED_RUN``)."""
    data = status.get("data") or {}
    if data.get(_FAILED_RUN, "") not in ("", data.get("jobUid", "")):
        return {}
    return _parse_failed(data.get(_FAILED) or "")


def _parse_failed(text: str) -> dict[str, str]:
    """A ``failedVolumes`` value as ``{id: reason}``, in its own order. A
    value that is not the list this module writes is no failures at all."""
    try:
        listed = json.loads(text or "[]")
    except ValueError:
        return {}
    if not isinstance(listed, list):
        return {}
    return {
        entry["id"]: str(entry.get("reason", ""))
        for entry in listed
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _union_failed(stored: str, fresh: str) -> str:
    """Two ``failedVolumes`` lists as one, per volume id (3075). Each detail
    request names the failures whose pods still exist, so two of them a day
    apart can name disjoint sets -- and the later one replacing the earlier
    lost every reason in it. The fresh observation leads and its reason
    wins, except that a blank one (a pod collected since) never erases a
    sentence the record already has."""
    merged = _parse_failed(fresh)
    for vol_id, reason in _parse_failed(stored).items():
        if not merged.get(vol_id):
            merged[vol_id] = reason
    return _capped([{"id": i, "reason": r} for i, r in merged.items()])


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
    cached_progress=None,
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
    lines = _volume_lines(record)
    named = {line.split("\t", 1)[0] for line in lines} & reasons.keys()
    # The record counts more failures than it names -- pods collected
    # before anyone opened the page, or more than it keeps. Which of the
    # rest failed is written down nowhere, so none of them is `done` (3074).
    if ending == "done" and len(named) < row["counts"]["failed"]:
        ending = UNKNOWN_STATE
    volumes: list[dict] = []
    for idx, line in enumerate(lines):
        vol_id = line.split("\t", 1)[0]
        state = "failed" if vol_id in reasons else ending
        volume = _volume_row(idx, line, state, results_base, pipeline, cfg)
        if reasons.get(vol_id):
            volume["reason"] = {
                "stage": None,
                "permanent": None,
                "error": reasons[vol_id],
            }
        volumes.append(volume)

    failures = _failures(volumes)
    page = volumes[offset : offset + limit]
    latest = _latest(volumes)
    pages = _read_progress(
        volumes,
        page,
        [*failures, *([latest] if latest else [])],
        _internal_results_base(row["namespace"], pipeline, cfg),
        fetch_progress or (lambda *_args: None),
        cached_progress or _not_cached,
    )
    return {
        **row,
        **pages,
        "qualityPrediction": quality_prediction(pipeline_configmap),
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
    they do not see the same clock). ``failedVolumes`` is merged per volume
    rather than replaced (``_union_failed``).
    """
    merged = dict(stored)
    for key, value in fresh.items():
        old = stored.get(key, "")
        if value in _SAYS_NOTHING and old not in _SAYS_NOTHING:
            continue
        if key == "finishedAt" and not _is_later(value, old):
            continue
        merged[key] = _union_failed(old, value) if key == "failedVolumes" else value
    return merged


def instant(text: str) -> datetime | None:
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
    fresh, stored = instant(value), instant(old)
    if fresh is None:
        return False
    return stored is None or fresh >= stored


def status_configmap(row: dict, data: dict[str, str], labels: bool = True) -> dict:
    """The whole object the read API applies. Named after the campaign
    ConfigMap beside it and labelled like it, so a prune takes both."""
    meta: dict = {
        "name": f"campaign-{row['name']}{STATUS_SUFFIX}",
        "namespace": row["namespace"],
    }
    if labels:
        meta["labels"] = {
            _MANAGED_BY_LABEL: "converter",
            _CAMPAIGN_LABEL: row["campaign"],
            _PIPELINE_LABEL: row["pipeline"],
            KIND_LABEL: STATUS_KIND,
        }
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": data}


#: `htrflow-campaigns apply`'s field manager (converter ``cluster.FIELD_MANAGER``).
APPLY_MANAGER = "htrflow-campaigns"
#: This API's own two. ``failedVolumes`` has a manager of its own, written
#: by the detail route alone -- the only one that reads pods and so the only
#: one with anything new to say about it. Sent by both routes under one
#: manager, a list request re-sent the value it had read and could apply it
#: over reasons a detail request had written a moment before (2026-09-23
#: audit).
WEB_MANAGER = "htrflow-web"
FAILURES_MANAGER = "htrflow-web-failures"
_FAILED = "failedVolumes"
#: The Job ``failedVolumes`` is about, written beside it by the same manager.
#: A summary can move to a recreated Job without the failures moving with it
#: -- an older pod mid rolling update does exactly that -- and failures of
#: another run are neither read as this one's nor merged into them
#: (2026-09-23 review).
_FAILED_RUN = "failedVolumesJobUid"


class RecordWrite(NamedTuple):
    """One server-side apply of the status ConfigMap: the object, whether it
    is forced, and the field manager it is sent as."""

    body: dict
    force: bool
    manager: str


def _owned_keys(meta: dict, manager: str) -> set[str]:
    """The ``data`` keys ``manager`` owns in the stored record, as the API
    server wrote them down in its ``managedFields``."""
    keys: set[str] = set()
    for entry in meta.get("managedFields") or []:
        if entry.get("manager") == manager and entry.get("operation") == "Apply":
            data = (entry.get("fieldsV1") or {}).get("f:data") or {}
            keys |= {k.removeprefix("f:") for k in data}
    return keys


def record_write(
    stored: dict | None, row: dict, fresh: dict[str, str]
) -> list[RecordWrite]:
    """What to apply over the ``stored`` status ConfigMap, in order -- none
    when it already says all of it. The summary first, then (detail route
    only: ``fresh`` carries ``failedVolumes``) the failures.

    Who owns what (3075, 3081). ``jobUid`` names the Job the record is
    about. `htrflow-campaigns apply` writes the ending it reads off that
    Job -- the summary fields, ``jobUid`` and the labels -- forced, once the
    Job is over; that is authoritative, and once apply owns ``phase`` for
    this Job the summary sends only the keys apply does not own: server-side
    apply keeps a field another manager still owns when this one leaves it
    out, so nothing is lost by omission and nothing is left to conflict
    over. Until then the summary is the whole observation, merged over what
    is stored (``merge_record``). A record of ANOTHER Job -- one reaped, then
    recreated under the same name -- says nothing about this run: it is
    replaced whole, and forced when another manager still owns some of the
    old run's fields, on the ``resourceVersion`` this request read, so an
    ending apply writes in between wins (a 409). A record without a
    ``jobUid`` predates it and counts as this Job's.

    ``failedVolumes`` is ``FAILURES_MANAGER``'s alone (``_failures_write``),
    with the Job it is about beside it (``_FAILED_RUN``), so a record that
    moved to another Job leaves the old run's failures standing and unread.
    The summary leaves it out -- except while ``WEB_MANAGER`` still owns it
    from before it had a manager of its own: a manager that stops sending a
    field releases it, and a field nobody owns is deleted, so the summary
    keeps sending the stored value until the failures manager has taken the
    field over (forced). A list request that read the value before that
    take-over then conflicts rather than overwrites.
    """
    data = (stored or {}).get("data") or {}
    meta = (stored or {}).get("metadata") or {}
    fresh = dict(fresh)
    failed = fresh.pop(_FAILED, None)
    other_job = data.get("jobUid", "") not in ("", fresh["jobUid"])
    writes = []
    summary = _summary_write(data, meta, row, fresh, other_job)
    if summary is not None:
        writes.append(summary)
    if failed is not None:
        failures = _failures_write(data, meta, row, failed, fresh["jobUid"])
        if failures is not None:
            writes.append(failures)
    return writes


def _summary_write(
    data: dict[str, str],
    meta: dict,
    row: dict,
    fresh: dict[str, str],
    other_job: bool,
) -> RecordWrite | None:
    """``WEB_MANAGER``'s apply: every field but the failures."""
    kept = {k: v for k, v in data.items() if k not in (_FAILED, _FAILED_RUN)}
    body = fresh if other_job else merge_record(kept, fresh)
    if not other_job and _FAILED in _owned_keys(meta, WEB_MANAGER) & data.keys():
        body = {**body, _FAILED: data[_FAILED]}
    theirs = _owned_keys(meta, APPLY_MANAGER)
    if not other_job and "phase" in theirs:
        body = {k: v for k, v in body.items() if k not in theirs}
        if all(data.get(k) == v for k, v in body.items()):
            return None
        return RecordWrite(
            status_configmap(row, body, labels=False), False, WEB_MANAGER
        )
    if not other_job and all(data.get(k) == v for k, v in body.items()):
        return None
    force = bool(theirs)
    cm = status_configmap(row, body)
    if force:
        cm["metadata"]["resourceVersion"] = meta.get("resourceVersion", "")
    return RecordWrite(cm, force, WEB_MANAGER)


def _failures_write(
    data: dict[str, str], meta: dict, row: dict, failed: str, job_uid: str
) -> RecordWrite | None:
    """``FAILURES_MANAGER``'s apply: ``failedVolumes`` and the Job it is
    about, merged per volume with what is stored (``merge_record``) -- or,
    when what is stored is another run's, this run's alone. Forced: no
    other manager has anything to say about the field, and one that owns it
    still -- this API's summary manager, from before the split -- has to
    give it up.

    Held to the ConfigMap this request read, by its uid: an apply with a
    uid never creates an object, so a record `apply --prune` deleted in
    between stays deleted -- sent bare, this write re-created it with no
    labels, out of reach of the list and the prune (2026-09-23 review).
    ``None`` when there was no record to read: the summary write before it
    creates one, and ``app.py`` holds this write to that one's uid."""
    ours = data.get(_FAILED_RUN) or data.get("jobUid") or job_uid
    old = {_FAILED: data[_FAILED]} if ours == job_uid and _FAILED in data else {}
    value = merge_record(old, {_FAILED: failed})[_FAILED]
    owned = _FAILED in _owned_keys(meta, FAILURES_MANAGER)
    if owned and (data.get(_FAILED), data.get(_FAILED_RUN)) == (value, job_uid):
        return None
    body = status_configmap(row, {_FAILED: value, _FAILED_RUN: job_uid}, labels=False)
    body["metadata"]["uid"] = meta.get("uid")
    return RecordWrite(body, True, FAILURES_MANAGER)


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
    return source if browser_http_url(source) else None


#: A host label this API is sure every browser's URL parser takes as it is:
#: letters, digits, `-` and `_`.
_LABEL = re.compile(r"[A-Za-z0-9_-]+")
#: WHATWG reads a host whose last label is a number as an IPv4 address, and
#: rejects it if that address is not one (`example.123`, `1.2.3.999`).
_NUMERIC = re.compile(r"(0[xX][0-9A-Fa-f]*|[0-9]+)")


def browser_http_url(value: str) -> bool:
    """Whether ``value`` is an absolute http(s) URL the WHATWG URL parser --
    `new URL()` in the browser -- takes. A subset, on purpose: anything this
    cannot be sure of is refused, which costs a volume its source link, and
    a URL the browser refuses cost the card itself (2026-09-23 audit: the
    page parses a detail all or nothing). Checked here: the scheme, no
    whitespace, control or backslash anywhere, a port in range, and a host
    that is a valid IPv6 literal, a valid dotted IPv4 address, or labels of
    letters, digits, `-` and `_` -- where a punycode label must decode to
    the non-ASCII letters it stands for, and a non-ASCII one must be letters
    and digits."""
    if not value.lower().startswith(("http://", "https://")):
        return False
    if any(ord(c) <= 0x20 or ord(c) == 0x7F or c == "\\" for c in value):
        return False
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises for a port out of range or not a number
    except ValueError:  # a bracketed host that is no address, a bad port
        return False
    host = parts.netloc.rpartition("@")[2]
    if host.startswith("["):
        literal, _, port = host[1:].partition("]")
        ok = port == "" or port.startswith(":")
        return ok and _address(ipaddress.IPv6Address, literal)
    host = host.partition(":")[0].removesuffix(".")
    labels = host.split(".")
    if "" in labels:  # no host at all, or an empty label
        return False
    if _NUMERIC.fullmatch(labels[-1]):
        return _address(ipaddress.IPv4Address, host)
    return all(_label(label) for label in labels)


def _label(label: str) -> bool:
    if label.isascii():
        if not _LABEL.fullmatch(label):
            return False
        if not label.lower().startswith("xn--"):
            return True
        try:
            decoded = label[4:].encode("ascii").decode("punycode")
        except UnicodeError:
            return False
        # What it decodes to must be what a browser would have encoded:
        # non-ASCII, already in its mapped form, and spelled the one way.
        return (
            not decoded.isascii()
            and _letters(decoded)
            and decoded == unicodedata.normalize("NFKC", decoded.casefold())
            and decoded.encode("punycode").decode("ascii") == label[4:].lower()
        )
    # A label that says it is punycode has to be ASCII.
    return not label.lower().startswith("xn--") and _letters(label)


def _letters(label: str) -> bool:
    """ASCII letters, digits and `-`, and non-ASCII letters written left to
    right. A right-to-left letter or a non-ASCII digit brings in the IDNA
    bidi rule, which a browser enforces and this does not try to."""
    return all(
        (c.isascii() and (c.isalnum() or c == "-"))
        or (c.isalpha() and unicodedata.bidirectional(c) == "L")
        for c in label
    )


def _address(kind: type, text: str) -> bool:
    """Whether ``text`` is an ``ipaddress.IPv4Address``/``IPv6Address``. A
    zone id (`%eth0`) is refused: Python takes one and WHATWG does not."""
    try:
        kind(text)
    except ValueError:
        return False
    return "%" not in text


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
        return _stopped(status)
    return _name_the_deadline(_reason(message), status.get("reason"))


def _stopped(status: dict) -> dict | None:
    """Why a pod stopped when nothing in it left a message: an OOM kill, an
    eviction, a kill before the wrapper could write one. The pod's own
    ``status.reason`` first (`Evicted`, `DeadlineExceeded`: said about the
    pod, and more telling than the exit code it caused), else the kubelet's
    reason and exit code for a container that stopped non-zero. Without
    this such an index was `failed` with no reason, and no reason kept it
    out of ``failures`` and so out of the record (3074)."""
    if status.get("reason"):
        return {"stage": None, "permanent": None, "error": status["reason"]}
    for cs in [
        *(status.get("containerStatuses") or []),
        *(status.get("initContainerStatuses") or []),
    ]:
        term = (cs.get("state") or {}).get("terminated") or (
            cs.get("lastState") or {}
        ).get("terminated")
        if term and term.get("exitCode"):
            said = f"{term.get('reason') or 'Error'} (exit code {term['exitCode']})"
            return {"stage": None, "permanent": None, "error": said}
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


def _terminations(statuses: list[dict] | None) -> list[dict]:
    return [
        {
            key: cs[key]
            for key in ("name", "state", "lastState")
            if key in cs and (key == "name" or "terminated" in (cs[key] or {}))
        }
        for cs in statuses or []
    ]


def pod_fields(pod: dict) -> dict:
    """Everything this module reads off a pod, and nothing else: its index
    label and creation time (``_pods_by_index``, ``newest``) and how it
    stopped (``wrapper_reason``). ``kube.list_pods`` keeps only this of each
    pod it reads, so a campaign with thousands of retries left behind holds
    a few hundred bytes per pod rather than its whole spec (2026-09-23
    audit) -- a field read here and dropped there fails the test that
    projects both."""
    meta = pod.get("metadata") or {}
    status = pod.get("status") or {}
    slim_status = {
        key: _terminations(status.get(key))
        for key in ("containerStatuses", "initContainerStatuses")
        if key in status
    }
    if "reason" in status:
        slim_status["reason"] = status["reason"]
    return {
        "metadata": {
            key: meta[key]
            for key in ("name", "creationTimestamp", "labels")
            if key in meta
        },
        "status": slim_status,
    }


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
    # Encoded into every URL, the way progress.py encodes its own: the id
    # came off a campaign's volumes.txt, a file people edit in a git repo,
    # and `../` in one walked out of the campaign's prefix in the href a
    # reader clicks (2026-09-14 review). The `id` field stays as written.
    key = quote(vol_id, safe="")
    return {
        "index": idx,
        "id": vol_id,
        "state": state,
        "manifestUrl": f"{results_base}/{key}/manifest.json",
        "iiifUrl": f"{results_base}/{key}/iiif.json",
        "altoPrefix": f"{results_base}/{key}/alto/",
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
    return (
        f"{cfg.public_results_base}/status/logs/"
        f"{quote(pipeline, safe='')}/{quote(volume_id, safe='')}.txt"
    )


def _pipeline_yaml(configmap: dict | None) -> str:
    text = ((configmap or {}).get("data") or {}).get("pipeline.yaml", "")
    # The API server sends strings; a hand-built object may not, and a
    # non-string reaches the parser as a stream it cannot read.
    return text if isinstance(text, str) else ""


#: A real pipeline is a few KB. Past this the text is not parsed at all:
#: the list route parses every pipeline ConfigMap on every request, so one
#: object near the 1 MiB ConfigMap limit is cost the whole list would pay.
MAX_PIPELINE_YAML = 64 * 1024


def _pipeline_steps(text: str) -> list[str]:
    """The ``step`` name of each entry under ``steps:``, in order — what the
    card's pipeline chip lists in its tooltip. A ConfigMap that is missing,
    empty or shaped differently is no steps rather than an error: the chip
    then just names the pipeline, and the campaign is unaffected either way."""
    if len(text) > MAX_PIPELINE_YAML:
        return []
    # Any exception from the parse is no steps, never an error, and the
    # types are not enumerated: this text is operator-editable and parsed on
    # every list request, and PyYAML raises assorted types on malformed
    # input (YAMLError, ValueError from a `2026-99-99` date, AttributeError
    # from a `!!timestamp` it cannot match, RecursionError -- an Exception
    # subclass -- from deep nesting). One bad ConfigMap must not fail the
    # list. Only the parse is guarded; the extraction below is ours.
    try:
        doc = yaml.safe_load(text)
    except Exception:
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


def quality_prediction(configmap: dict | None) -> bool:
    """Whether a pipeline ConfigMap's YAML has a QualityPrediction step --
    by its lower-cased name, the way htrflow resolves a step. A ConfigMap
    that is missing or unreadable scores nothing, never an error: the card
    then simply has no quality column."""
    steps = _pipeline_steps(_pipeline_yaml(configmap))
    return any(step.lower() == "qualityprediction" for step in steps)


def _failures(volumes: list[dict]) -> list[dict]:
    """The newest failed rows, newest index first -- every one of them,
    with a reason or without: a failure nobody could explain is still a
    failure, and the record written from this list is all a reaped campaign
    has to tell its failed volumes from its done ones (3074)."""
    failed = [v for v in volumes if v["state"] == "failed"]
    return sorted(failed, key=lambda v: v["index"], reverse=True)[:_MAX_FAILURES]


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


#: Cost cap (finding 5): bucket GETs one request may make. Only a GET
#: counts -- an answer already in ProgressReader's cache costs nothing (a
#: finished volume's is kept for the hour), so the campaign's totals are
#: every run volume's once each has been read once, however many there are,
#: while no request ever makes more than this many sequential GETs. At the
#: page's poll interval that reads a few thousand volumes' files in under an
#: hour of one reader's polling (3076).
PROGRESS_FETCH_CAP = 100

#: Seconds the whole fan-out may take, cap or no cap. Each fetch is one
#: sequential GET with progress.py's own short timeout, so an unreachable
#: bucket cost cap x that timeout -- over a minute of one worker, inside a
#: sync handler, and forty such requests emptied the threadpool /healthz is
#: answered from (2026-09-14 audit). Past the deadline the remaining rows
#: are answered with no progress, which is what an unreadable file already
#: means: a decoration missing from a page that still draws.
PROGRESS_FETCH_BUDGET = 5.0


def _read_progress(
    volumes: list[dict],
    page: list[dict],
    lead: list[dict],
    results_base: str,
    fetch,
    cached,
) -> dict:
    """Give every run volume its ``progress``, and sum them into the
    campaign's page totals (``_campaign_pages``) plus how many of the run
    volumes those totals cover.

    Summed over the rows the response carried, the totals were the page's,
    not the campaign's: they moved between polls, and a lost page in a
    volume past the page read as a clean campaign (3076). Every run volume
    is read now, in this order -- running ones (theirs is the progress
    actually changing), then the failures and ``latest`` (``lead``), then
    the ``page``, then the rest -- from the cache when it has the answer, and over the
    network only while the cap and the deadline allow. ``pagesCoverage``
    says how many were read, so the page never calls a campaign clean on
    totals that are not yet everyone's. ``fetch``/``cached`` are passed in
    (progress.py does the HTTP) so this module stays pure."""
    ahead, shown = {id(row) for row in lead}, {id(row) for row in page}
    order = sorted(  # stable: by index within each band
        (row for row in volumes if row["state"] != "pending"),
        key=lambda r: (r["state"] != "active", id(r) not in ahead, id(r) not in shown),
    )
    deadline = time.monotonic() + PROGRESS_FETCH_BUDGET
    misses = counted = 0
    for row in order:
        known, row["progress"] = cached(results_base, row["id"], row["state"])
        if not known and misses < PROGRESS_FETCH_CAP and time.monotonic() < deadline:
            misses += 1
            row["progress"] = fetch(results_base, row["id"], row["state"])
            # A GET that failed is not an answer; an absent file is.
            known = (
                row["progress"] is not None
                or cached(results_base, row["id"], row["state"])[0]
            )
        counted += known
    for row in [*page, *lead]:
        row.setdefault("progress", None)  # pending: nothing to read
    return {
        **_campaign_pages([row for row in order if row["progress"]]),
        "pagesCoverage": {"counted": counted, "of": len(order)},
    }


def _not_cached(*_args) -> tuple[bool, None]:
    return False, None


#: The campaign's lowest pages, across every volume read.
CAMPAIGN_LOWEST = 5


def _campaign_quality(rows: list[dict]) -> dict | None:
    """The campaign's predicted quality over the volumes whose progress
    carries one: the mean weighted by each volume's scored pages, and the
    worst pages anywhere, each with the viewer manifest it opens in.
    ``volumes`` says how many volumes it covers; the card says so when that
    is not all of them."""
    scored = [
        (row, q)
        for row in rows
        if (q := (row.get("progress") or {}).get("quality")) is not None
    ]
    if not scored:
        return None
    pages = sum(q["scored"] for _, q in scored)
    lowest = sorted(
        (
            {"volume": row["id"], **entry, "iiifUrl": row["iiifUrl"]}
            for row, q in scored
            for entry in q["lowest"]
        ),
        key=lambda e: (e["quality"], e["volume"], e["page"]),
    )[:CAMPAIGN_LOWEST]
    return {
        "mean": round(sum(q["mean"] * q["scored"] for _, q in scored) / pages, 4),
        "min": min(q["min"] for _, q in scored),
        "scored": pages,
        "volumes": len(scored),
        "lowest": lowest,
    }


def _campaign_pages(rows: list[dict]) -> dict:
    """What the card says above its table, summed over the volumes whose
    progress was read. ``lastError`` is the most recent page failure among
    them, carrying the volume it happened in and that volume's run log: the
    row it came from is often outside the page the reader is looking at."""
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
        "quality": _campaign_quality(rows),
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
    cached_progress=None,
) -> dict:
    """``JobDetail``: ``JobSummary`` plus per-index rows and top failures for
    ``GET /api/v1/jobs/{ns}/{name}``, paged by index. ``warmup`` passes
    through to ``summarize`` unchanged (Task 28); ``fetch_progress`` is
    ``(results_base, volume id, state) -> progress | None``
    (``app.py`` wires ``progress.ProgressReader.fetch``, and its ``cached``
    as ``cached_progress``, ``(...) -> (known, progress)``) -- called with the
    INTERNAL results base (this pod's own way to the bucket), never the
    public one every URL below is built from: on the PoC they are not the
    same address (docs: development/local-k3s)."""
    summary = summarize(job, cfg, warmup, quality_prediction(pipeline_configmap))
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
        # A done index's pods are history: no Succeeded pod is listed
        # (kube.list_pods), so the newest one left is an attempt that failed
        # before the one that published, and its sentence is not why the
        # volume is anything (2026-09-23 audit).
        if idx in pods_by_index and state != "done":
            newest_pod = newest(pods_by_index[idx])
            reason = wrapper_reason(newest_pod)
            if reason is not None:
                row["reason"] = reason
        volumes.append(row)

    failures = _failures(volumes)
    page = volumes[offset : offset + limit]
    pages = _read_progress(
        volumes,
        page,
        [*failures, *([latest] if (latest := _latest(volumes)) else [])],
        internal_base,
        fetch_progress or (lambda *_args: None),
        cached_progress or _not_cached,
    )

    pipeline_yaml = _pipeline_yaml(pipeline_configmap)
    return {
        **summary,
        **pages,
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
