"""ConfigMaps, warm-up Jobs and campaign Indexed Jobs, patched from packaged
YAML skeletons in ``manifests/`` (spec §3)."""

from __future__ import annotations

import copy
import functools
import re
from importlib import resources

import yaml

from .models import (
    DNS_LABEL,
    STATUS_SUFFIX,
    WARMUP_PREFIX,
    Campaign,
    ConverterConfig,
    Pipeline,
    Volume,
    parse_source_line,
)

_LABEL_JUNK = re.compile(r"[^A-Za-z0-9_.-]")
_PATH_RE = re.compile(r"[^.\[\]]+|\[\d+\]")
MAX_VOLUMES_PER_JOB = 10_000
#: Bytes of ``volumes.txt`` one part may carry. The API server sums the
#: values under ``data`` and ``binaryData`` -- nothing else, not the keys,
#: the metadata or the managed fields -- and refuses a ConfigMap over 1 MiB
#: (``Too long: may not be more than 1048576 bytes``). The 148 KiB left below
#: that is margin, not accounting: the same request also carries the object
#: around the value and the campaign's Job (a 3 MiB request cap), and a
#: budget set at the hard limit would leave a line-format change nowhere to
#: go. Counting volumes alone is not enough: an ``images:`` volume is ONE
#: line of space-joined URLs (``Volume.source_line``), so 300 pages of a
#: 74-character URL is 22.5 kB on that line and 47 such volumes exceed 1 MiB.
MAX_BYTES_PER_JOB = 900 * 1024

# Label/annotation keys below are set by direct dict indexing, never through
# ``_set``: they contain literal ``.`` characters (a real Kubernetes label
# key), which ``_set``'s dotted-path parser would otherwise split on.
_MANAGED_BY_LABEL = "htrflow.riksarkivet.se/managed-by"
#: The label every rendered object carries and the one an ``apply --prune``
#: deletes a cancelled campaign's leftovers by. The
#: only definition: ``cluster.py`` lists by it, the Makefile asks this
#: module for it, and ``test_render.py`` asserts the renderer writes it.
CAMPAIGN_SELECTOR = f"{_MANAGED_BY_LABEL}=converter"
#: Which campaign an object belongs to. ``cli`` reads it off an earlier
#: render to tell whose ``-partN`` files are whose.
CAMPAIGN_LABEL = "htrflow.riksarkivet.se/campaign"
_PIPELINE_LABEL = "htrflow.riksarkivet.se/pipeline"
_QUEUE_LABEL = "kueue.x-k8s.io/queue-name"
_PRIORITY_LABEL = "kueue.x-k8s.io/priority-class"
_SHA_ANNOTATION = "htrflow.riksarkivet.se/pipeline-sha256"
#: On the warm-up's POD template, so any recipe change -- the steps or the
#: image -- changes the template and the apply replaces the warm-up Job.
_RECIPE_ANNOTATION = "htrflow.riksarkivet.se/recipe-sha256"
_DIGEST_ANNOTATION = "htrflow.riksarkivet.se/image-digest"


def label_value(text: str) -> str:
    return _LABEL_JUNK.sub("-", text)[:63].strip("-_.")


def split(
    volumes: list[Volume],
    size: int = MAX_VOLUMES_PER_JOB,
    max_bytes: int = MAX_BYTES_PER_JOB,
) -> list[list[Volume]]:
    """The volume list cut into parts that fit one Job's ConfigMap: by count
    and by the bytes those volumes take up in ``volumes.txt``. A volume whose
    own line is over the budget still gets a part to itself -- a line is the
    smallest thing there is to cut."""
    parts: list[list[Volume]] = [[]]
    used = 0
    for v in volumes:
        size_of = len(v.source_line().encode()) + 1  # + the newline it ends on
        if parts[-1] and (len(parts[-1]) >= size or used + size_of > max_bytes):
            parts.append([])
            used = 0
        parts[-1].append(v)
        used += size_of
    return parts


@functools.lru_cache(maxsize=None)
def _base(name: str) -> dict:
    text = (resources.files("htrflow_converter") / "manifests" / name).read_text()
    return yaml.safe_load(text)


def _load(name: str) -> dict:
    """A fresh deep copy of the packaged manifest skeleton ``name``."""
    return copy.deepcopy(_base(name))


def _set(obj: dict, path: str, value: object) -> None:
    """Set ``obj``'s dotted/``[i]``-indexed ``path`` in place. Every
    intermediate segment must already exist (a typo raises instead of
    silently creating nested structure); only the final segment may be new.
    Not for a key that itself contains a literal ``.`` -- index those
    directly (see the module-level label/annotation constants)."""
    parts = _PATH_RE.findall(path)
    for part in parts[:-1]:
        obj = obj[int(part[1:-1])] if part[0] == "[" else obj[part]
    last = parts[-1]
    if last[0] == "[":
        obj[int(last[1:-1])] = value
    else:
        obj[last] = value


def _scheduling(job: dict, cfg: ConverterConfig) -> None:
    """Where this Job's pod may run: the GPU RuntimeClass, the node labels
    and the taints it tolerates. The warm-up Job needs all three as much as
    the campaign Job does -- it is the one pod that mounts its recipe's
    directory of the model cache read-write, so a warm-up scheduled past a
    taint onto some other node
    fills a *different* ReadWriteOnce volume and the marker never appears
    where the batch pods are waiting for it."""
    if cfg.runtime_class:
        _set(job, "spec.template.spec.runtimeClassName", cfg.runtime_class)
    if cfg.node_selector:
        _set(job, "spec.template.spec.nodeSelector", dict(cfg.node_selector))
    if cfg.tolerations:
        _set(
            job,
            "spec.template.spec.tolerations",
            [t.manifest() for t in cfg.tolerations],
        )


def _mount_cache_dir(job: dict, p: Pipeline) -> None:
    """Every container's mount of the model-cache PVC (``data``) narrowed to
    ``p``'s own directory on it (``Pipeline.cache_dir``). Inside the pod
    nothing moves -- ``HF_HOME`` is still ``/data/hf`` and the marker still
    ``/data/warmup/<id>.done`` -- so the wrapper needs no change; what moves
    is which directory of the volume ``/data`` is. The kubelet creates it on
    first mount, and it cannot be escaped from inside the pod."""
    pod = job["spec"]["template"]["spec"]
    for container in pod.get("initContainers", []) + pod["containers"]:
        for mount in container["volumeMounts"]:
            if mount["name"] == "data":
                mount["subPath"] = p.cache_dir


def _pipeline_configmap(p: Pipeline, cfg: ConverterConfig) -> dict:
    cm = _load("pipeline-configmap.yaml")
    _set(cm, "metadata.name", f"htr-pipeline-{p.id}")
    _set(cm, "metadata.namespace", cfg.namespace)
    cm["metadata"]["labels"][_PIPELINE_LABEL] = label_value(p.id)
    cm["metadata"]["annotations"][_SHA_ANNOTATION] = p.sha256
    cm["data"]["pipeline.yaml"] = p.pipeline_yaml()
    return cm


def _warmup_job(p: Pipeline, cfg: ConverterConfig) -> dict:
    job = _load("warmup-job.yaml")
    _set(job, "metadata.name", f"{WARMUP_PREFIX}{p.id}")
    _set(job, "metadata.namespace", cfg.namespace)
    job["metadata"]["labels"][_PIPELINE_LABEL] = label_value(p.id)
    job["spec"]["template"]["metadata"]["annotations"] = {
        _RECIPE_ANNOTATION: p.recipe_sha256
    }
    _set(job, "spec.template.spec.containers[0].image", p.image)
    _mount_cache_dir(job, p)
    for e in job["spec"]["template"]["spec"]["containers"][0]["env"]:
        if e["name"] == "PIPELINE_ID":
            e["value"] = p.id
    if cfg.hf_token_secret:
        # The one pod that reaches HF Hub is the one pod that gets a Hub
        # credential. The campaign Job deliberately gets nothing: it runs
        # `HF_HUB_OFFLINE=1` against the cache this Job filled and the
        # `htr-batch-job` NetworkPolicy gives it no route to the Hub, so a
        # token there would be a secret in a pod that cannot spend it.
        # `optional: false` -- a missing Secret must keep the pod from
        # starting, rather than let it download anonymously and fail later,
        # on the private model, with a 404 that names no cause.
        job["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {
                "name": "HF_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": cfg.hf_token_secret,
                        "key": "token",
                        "optional": False,
                    }
                },
            }
        )
    _set(job, "spec.template.spec.volumes[0].configMap.name", f"htr-pipeline-{p.id}")
    _set(
        job,
        "spec.template.spec.volumes[1].persistentVolumeClaim.claimName",
        cfg.data_pvc,
    )
    _scheduling(job, cfg)
    return job


def pipeline_objects(p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    return [_pipeline_configmap(p, cfg), _warmup_job(p, cfg)]


def recipe(objects: list[dict]) -> dict[str, object]:
    """What a pipeline's rendered pair records about the RECIPE itself: the
    steps it runs and the image it runs them in. The two things
    ``pipelines/<id>.yaml`` alone decides, and what ``cli`` holds a re-render
    against to keep a pipeline id a permanent name for one recipe.

    The steps come back PARSED, not as the sha256 the ConfigMap is annotated
    with and not as the YAML text it is taken over: ``settings:`` written
    above ``step:`` is the same recipe, and a PyYAML release that spells a
    mapping differently would otherwise report every untouched pipeline in
    the repo as changed.

    Deliberately not the whole rendered file, either. A converter release or
    a converter.yaml setting moves the warm-up's pod template too (the GPU
    RuntimeClass, the Hub token's env var) without changing the recipe by a
    word -- ``apply`` replaces a warm-up Job for those.
    """
    cm = next((o for o in objects if o.get("kind") == "ConfigMap"), {})
    job = next((o for o in objects if o.get("kind") == "Job"), {})
    pod = ((job.get("spec") or {}).get("template") or {}).get("spec") or {}
    containers = pod.get("containers") or [{}]
    parsed = yaml.safe_load((cm.get("data") or {}).get("pipeline.yaml") or "")
    return {
        "image": containers[0].get("image", ""),
        "steps": parsed.get("steps") if isinstance(parsed, dict) else None,
    }


def campaign_record(cm: dict) -> dict[str, object]:
    """What a campaign ConfigMap records about the campaign itself: its
    volumes (parsed, so two spellings of one list compare equal), its
    pipeline and its image -- ``None`` for what the object does not carry.
    ``cli`` holds a render against the LIVE ConfigMap by these (3084)."""
    meta = cm.get("metadata") or {}
    text = (cm.get("data") or {}).get("volumes.txt")
    return {
        "volumes": None
        if text is None
        else [parse_source_line(line) for line in text.splitlines() if line],
        "pipeline": (meta.get("labels") or {}).get(_PIPELINE_LABEL),
        "image": (meta.get("annotations") or {}).get(_DIGEST_ANNOTATION),
    }


_WAIT_STEP = 10
#: The `warmup-wait` gate, bounded. A batch pod reserves `nvidia.com/gpu: 1`
#: for its whole lifetime -- init containers included, and Kueue holds the
#: quota through it -- so an unbounded `until [ -f ... ]` costs one GPU for
#: the pod's entire `activeDeadlineSeconds`, once per retry, every time a
#: pipeline's warm-up did not write its marker. Exit 13 is the code the Job's
#: `podFailurePolicy` turns into `FailIndex`: no retry buys a marker that is
#: not coming. The message names the path, because the marker is the only
#: thing an operator can go and look at. The comparison is `-le`, not `-lt`:
#: the check runs before each sleep, so `-lt` would give up one step early
#: while printing the limit it did not reach.
_WARMUP_WAIT = (
    "n=0; until [ -f {marker} ]; do n=$((n+{step}));"
    ' [ "$n" -le {limit} ] || {{ echo "no warm-up marker at {marker} after'
    " {limit}s: the pipeline's warm-up Job has not finished\" >&2; exit 13; }};"
    " sleep {step}; done"
)


def _campaign_configmap(
    name: str, c: Campaign, p: Pipeline, volumes: list[Volume], cfg: ConverterConfig
) -> dict:
    # The labels are not decoration: `htrflow-campaigns apply --prune` finds
    # a deleted campaign's leftovers by listing
    # `htrflow.riksarkivet.se/managed-by=converter`. An unlabelled ConfigMap
    # would outlive the Job it fed.
    cm = _load("configmap.yaml")
    text = "\n".join(v.source_line() for v in volumes) + "\n" if volumes else ""
    _set(cm, "metadata.name", f"campaign-{name}")
    _set(cm, "metadata.namespace", cfg.namespace)
    cm["metadata"]["labels"][CAMPAIGN_LABEL] = label_value(c.name)
    cm["metadata"]["labels"][_PIPELINE_LABEL] = label_value(p.id)
    cm["metadata"]["annotations"][_DIGEST_ANNOTATION] = p.image
    cm["data"]["volumes.txt"] = text
    return cm


def _campaign_job(
    name: str, c: Campaign, p: Pipeline, volumes: list[Volume], cfg: ConverterConfig
) -> dict:
    job = _load("campaign-job.yaml")
    completions = len(volumes)
    # cfg.window is the per-cluster CAP: a campaign may ask for less, never
    # more. Kueue partial admission would shrink an oversized parallelism on
    # the live Job instead -- and then reject every later apply of the
    # unchanged rendered file (docs: development/e2e-indexed-jobs.md).
    parallelism = min(c.window or cfg.window, cfg.window)

    # ``name`` is the Job's own metadata.name (and the campaign ConfigMap's
    # name suffix). An object name is a DNS-1123 *subdomain* (<=253 chars),
    # but a JOB name does not get all of it: it becomes a label value and a
    # pod-name prefix, both DNS labels -- ``campaign_names`` caps it. The
    # label VALUES below go through label_value() for the same reason.
    _set(job, "metadata.name", name)
    _set(job, "metadata.namespace", cfg.namespace)
    labels = job["metadata"]["labels"]
    labels[CAMPAIGN_LABEL] = label_value(c.name)
    labels[_PIPELINE_LABEL] = label_value(p.id)
    labels[_QUEUE_LABEL] = cfg.queue
    if c.priority:
        labels[_PRIORITY_LABEL] = c.priority

    _set(job, "spec.completions", completions)
    _set(job, "spec.parallelism", parallelism)
    _set(job, "spec.maxFailedIndexes", completions)
    # The skeleton carries `spec.suspend: false` as spec's first key so a
    # paused campaign's rendered Job keeps `suspend` in the same place a
    # hand-built `{"suspend": True, **spec}` used to. An unpaused campaign
    # drops the placeholder again so its rendered Job has no `suspend` field
    # at all, matching every campaign that has never been paused.
    if c.suspend:  # intent; `htrflow-campaigns apply` enforces it under Kueue
        _set(job, "spec.suspend", True)
    else:
        job["spec"].pop("suspend", None)

    _set(job, "spec.template.spec.containers[0].image", p.image)
    dynamic_env = {
        "PIPELINE_ID": p.id,
        "S3_PREFIX": f"{cfg.namespace}/",
        "PUBLIC_RESULTS_BASE": cfg.public_results_base,
        "IMAGE_DIGEST": p.image,
        "MANIFEST_MAX_BYTES": str(cfg.manifest_max_bytes),
        "FETCH_MAX_BYTES": str(cfg.fetch_max_bytes),
    }
    for e in job["spec"]["template"]["spec"]["containers"][0]["env"]:
        if e["name"] in dynamic_env:
            e["value"] = dynamic_env[e["name"]]
        elif e["name"] in ("S3_ENDPOINT", "S3_BUCKET"):
            e["valueFrom"]["secretKeyRef"]["name"] = cfg.s3_secret

    # The per-volume budget is the pod's own deadline, not a wrapper env var
    # (docs: how-it-works/failure-handling).
    deadline = p.max_seconds or cfg.max_seconds
    _set(job, "spec.template.spec.activeDeadlineSeconds", deadline)
    ttl = p.ttl_seconds_after_finished or cfg.ttl_seconds_after_finished
    _set(job, "spec.ttlSecondsAfterFinished", ttl)

    _set(job, "spec.template.spec.volumes[0].configMap.name", f"campaign-{name}")
    _set(job, "spec.template.spec.volumes[1].configMap.name", f"htr-pipeline-{p.id}")
    _set(
        job,
        "spec.template.spec.volumes[2].persistentVolumeClaim.claimName",
        cfg.data_pvc,
    )
    _set(job, "spec.template.spec.volumes[4].secret.secretName", cfg.s3_secret)

    _set(job, "spec.template.spec.initContainers[0].image", p.image)
    _mount_cache_dir(job, p)
    # Inside this recipe's own directory (`_mount_cache_dir`): the marker a
    # warm-up of an earlier recipe left is in another directory altogether.
    marker = f"/data/warmup/{p.id}.done"
    _set(
        job,
        "spec.template.spec.initContainers[0].command[2]",
        # Strictly shorter than the pod it runs in, by one sleep step. Past
        # `activeDeadlineSeconds` the kubelet kills the pod with 143, which
        # no `FailIndex` rule matches -- so the index would be retried,
        # holding its GPU again, with no sentence anywhere. Only the gate
        # produces exit 13, and the two clocks do not start together (the
        # pod's runs from pod start, the gate's from the init container's
        # first line), so a tie goes to the kubelet: the gate has to expire
        # first. The floor keeps a deadline under one step renderable.
        _WARMUP_WAIT.format(
            marker=marker,
            step=_WAIT_STEP,
            limit=min(cfg.warmup_wait_seconds, max(deadline - _WAIT_STEP, _WAIT_STEP)),
        ),
    )

    _scheduling(job, cfg)
    return job


#: What is left of a DNS label once the WIDEST part suffix and the widest pod
#: index are reserved: ``-9999`` because an Indexed Job's highest index is
#: ``MAX_VOLUMES_PER_JOB - 1``, and ``-part999`` because 999 parts is 878 MiB
#: of ``volumes.txt`` -- a campaign file no git repo is going to carry. The
#: reservation is constant on purpose: measuring THIS render instead would
#: make a campaign's stem move when it grows from 9 parts to 10, renaming
#: every part out from under the Jobs already applied under them.
_SPLIT_STEM = DNS_LABEL - len("-part999") - len("-9999")


def split_stem(name: str) -> str:
    """The name a split campaign's parts are built from: ``<stem>-partN``,
    which with its pod index (``<name>-<index>``) has to stay inside a DNS
    label -- the API server refuses the Job otherwise ("will not able to
    create pod with invalid DNS label", and "spec.template.labels: must be
    no more than 63 bytes" for the job-name label the controller copies the
    name into). ``cli`` finds an earlier render by this stem."""
    return name[:_SPLIT_STEM]


def campaign_names(c: Campaign, parts: list[list[Volume]]) -> list[str]:
    """What this campaign's Jobs (and ConfigMaps) are called."""
    if len(parts) == 1:
        return [c.name]
    return [f"{split_stem(c.name)}-part{i}" for i in range(1, len(parts) + 1)]


def last_pod_problem(c: Campaign) -> str | None:
    """One sentence when the API server would refuse one of ``c``'s Jobs.

    It holds an Indexed Job's name to a DNS-1123 subdomain and the hostname
    of its LAST pod, ``<name>-<completions - 1>``, to a DNS-1123 label ("will
    not able to create pod with invalid DNS label"). ``models`` refuses the
    dots; the length depends on the volume count, so it is checked here, on
    the names this render gives. A part's stem leaves room for any index, so
    only a campaign that renders as one Job can fail it (3087)."""
    parts = split(c.volumes)
    for name, volumes in zip(campaign_names(c, parts), parts):
        last = f"{name}-{len(volumes) - 1}"
        if len(last) > DNS_LABEL:
            return (
                f"campaign {c.name} cannot be applied: its last pod would be "
                f"{last}, {len(last)} characters, and a pod's hostname stops "
                f"at {DNS_LABEL} — rename the file to at most "
                f"{DNS_LABEL - len(last) + len(name)} characters"
            )
    return None


#: The status ConfigMap's labels beside the campaign's own, and the one
#: that tells the two apart (packages/web ``projection.status_configmap``
#: writes the same object; a test asserts the field names agree).
_KIND_LABEL = "htrflow.riksarkivet.se/kind"
_STATUS_KIND = "status"


def status_configmap(live: dict, cfg: ConverterConfig) -> dict | None:
    """How a campaign ENDED, read off its live Job, or ``None`` while that
    Job is still running.

    The read API writes this record too, and in more detail -- it reads the
    pods, so it alone can say why a volume failed. But it writes it only
    while somebody has the status page open, and a campaign that finishes
    unwatched on a Friday is reaped before anyone looks: no terminal record,
    and the next apply recreates the Job and runs every volume again. The
    apply is the one thing guaranteed to run, so it records the ending it
    can see. Counts come from ``status.succeeded`` against ``completions``,
    not from the index ranges: for a Job that is over, every index that did
    not succeed failed, and ``status.failed`` counts pods (retries included),
    not indexes.
    """
    status = live.get("status") or {}
    conditions = status.get("conditions") or []
    done = status.get("succeeded") or 0
    complete = any(
        c.get("type") == "Complete" and c.get("status") == "True" for c in conditions
    )
    failed = any(
        c.get("type") == "Failed" and c.get("status") == "True" for c in conditions
    )
    if not complete and not failed:
        return None
    meta = live.get("metadata") or {}
    labels = meta.get("labels") or {}
    namespace = meta.get("namespace", "")
    pipeline = labels.get(_PIPELINE_LABEL, "")
    total = (live.get("spec") or {}).get("completions") or 0
    finished = status.get("completionTime") or next(
        (
            c.get("lastTransitionTime")
            for c in conditions
            if c.get("type") in ("Complete", "Failed") and c.get("status") == "True"
        ),
        None,
    )
    base = cfg.public_results_base
    cm = _load("configmap.yaml")
    _set(cm, "metadata.name", f"campaign-{meta.get('name', '')}{STATUS_SUFFIX}")
    _set(cm, "metadata.namespace", namespace)
    cm["metadata"]["labels"][CAMPAIGN_LABEL] = labels.get(CAMPAIGN_LABEL, "")
    cm["metadata"]["labels"][_PIPELINE_LABEL] = pipeline
    cm["metadata"]["labels"][_KIND_LABEL] = _STATUS_KIND
    cm["metadata"].pop("annotations")  # the digest is on the record, not here
    cm["data"] = {
        "phase": "Succeeded" if complete else ("PartiallyFailed" if done else "Failed"),
        "volumesTotal": str(total),
        "volumesDone": str(done),
        "volumesFailed": str(max(total - done, 0)),
        "startedAt": status.get("startTime") or "",
        "finishedAt": finished or "",
        # Stripped the way the read API strips its own base: the two write
        # this field, and a slash apart they disagree about it (3081).
        "resultsBase": f"{base.rstrip('/')}/{namespace}/{pipeline}",
        # Which Job ended so. A Job recreated under this name is another
        # run, and this is how the read API tells (3075).
        "jobUid": meta.get("uid", ""),
    }
    return cm


#: The one object in `rendered/` Argo CD syncs (3085). Every other one is a
#: Skip hook (manifests/campaign-job.yaml), so without this an Application
#: would never go OutOfSync -- and automated sync runs only then, so the
#: hook that runs `htrflow-campaigns apply` would never run. Unlabelled on
#: purpose: `apply --prune` deletes by the converter's label, and this is
#: Argo CD's to keep.
SYNC_CONFIGMAP = "htrflow-campaigns-render"


def sync_configmap(cfg: ConverterConfig, sha256: str) -> dict:
    """The render's digest, as a ConfigMap: changes when the render does."""
    meta = {"name": SYNC_CONFIGMAP, "namespace": cfg.namespace}
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta,
            "data": {"sha256": sha256}}  # fmt: skip


def campaign_objects(c: Campaign, p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    parts = split(c.volumes)
    objects: list[dict] = []
    for name, vols in zip(campaign_names(c, parts), parts):
        objects.append(_campaign_configmap(name, c, p, vols, cfg))
        objects.append(_campaign_job(name, c, p, vols, cfg))
    return objects
