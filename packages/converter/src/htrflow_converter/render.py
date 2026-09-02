"""ConfigMaps, warm-up Jobs and campaign Indexed Jobs, patched from packaged
YAML skeletons in ``manifests/`` (spec §3)."""

from __future__ import annotations

import copy
import functools
import re
from importlib import resources

import yaml

from .models import Campaign, ConverterConfig, Pipeline, Volume

_LABEL_JUNK = re.compile(r"[^A-Za-z0-9_.-]")
_PATH_RE = re.compile(r"[^.\[\]]+|\[\d+\]")
MAX_VOLUMES_PER_JOB = 10_000

# Label/annotation keys below are set by direct dict indexing, never through
# ``_set``: they contain literal ``.`` characters (a real Kubernetes label
# key), which ``_set``'s dotted-path parser would otherwise split on.
_CAMPAIGN_LABEL = "htrflow.riksarkivet.se/campaign"
_PIPELINE_LABEL = "htrflow.riksarkivet.se/pipeline"
_QUEUE_LABEL = "kueue.x-k8s.io/queue-name"
_PRIORITY_LABEL = "kueue.x-k8s.io/priority-class"
_SHA_ANNOTATION = "htrflow.riksarkivet.se/pipeline-sha256"


def label_value(text: str) -> str:
    return _LABEL_JUNK.sub("-", text)[:63].strip("-_.")


def split(volumes: list[Volume], size: int = MAX_VOLUMES_PER_JOB) -> list[list[Volume]]:
    if not volumes:
        return [[]]
    return [volumes[i : i + size] for i in range(0, len(volumes), size)]


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
    _set(job, "metadata.name", f"htr-warmup-{p.id}")
    _set(job, "metadata.namespace", cfg.namespace)
    job["metadata"]["labels"][_PIPELINE_LABEL] = label_value(p.id)
    _set(job, "spec.template.spec.containers[0].image", p.image)
    for e in job["spec"]["template"]["spec"]["containers"][0]["env"]:
        if e["name"] == "PIPELINE_ID":
            e["value"] = p.id
    _set(job, "spec.template.spec.volumes[0].configMap.name", f"htr-pipeline-{p.id}")
    _set(
        job,
        "spec.template.spec.volumes[1].persistentVolumeClaim.claimName",
        cfg.data_pvc,
    )
    return job


def pipeline_objects(p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    return [_pipeline_configmap(p, cfg), _warmup_job(p, cfg)]


def _campaign_configmap(
    name: str, c: Campaign, p: Pipeline, volumes: list[Volume], cfg: ConverterConfig
) -> dict:
    # The labels are not decoration: `kubectl apply --prune -l
    # htrflow.riksarkivet.se/managed-by=converter` (and Argo CD's own prune)
    # find a deleted campaign's leftovers by them. An unlabelled ConfigMap
    # would outlive the Job it fed.
    cm = _load("configmap.yaml")
    text = "\n".join(v.source_line() for v in volumes) + "\n" if volumes else ""
    _set(cm, "metadata.name", f"campaign-{name}")
    _set(cm, "metadata.namespace", cfg.namespace)
    cm["metadata"]["labels"][_CAMPAIGN_LABEL] = label_value(c.name)
    cm["metadata"]["labels"][_PIPELINE_LABEL] = label_value(p.id)
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
    # name suffix): a K8s object name is a DNS-1123 *subdomain* (<=253 chars,
    # no per-63-char label truncation), unlike the label VALUES below, which
    # go through label_value().
    _set(job, "metadata.name", name)
    _set(job, "metadata.namespace", cfg.namespace)
    labels = job["metadata"]["labels"]
    labels[_CAMPAIGN_LABEL] = label_value(c.name)
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
    if c.suspend:  # intent; scripts/kueue-pause-sync.sh enforces it under Kueue
        _set(job, "spec.suspend", True)
    else:
        job["spec"].pop("suspend", None)

    _set(job, "spec.template.spec.containers[0].image", p.image)
    prefix = "" if cfg.legacy_layout else f"{cfg.namespace}/"
    dynamic_env = {
        "PIPELINE_ID": p.id,
        "S3_PREFIX": prefix,
        "PUBLIC_RESULTS_BASE": cfg.public_results_base,
        "IMAGE_DIGEST": p.image,
        "MAX_SECONDS": str(p.max_seconds or cfg.max_seconds),
        "MANIFEST_MAX_BYTES": str(cfg.manifest_max_bytes),
        "FETCH_MAX_BYTES": str(cfg.fetch_max_bytes),
    }
    for e in job["spec"]["template"]["spec"]["containers"][0]["env"]:
        if e["name"] in dynamic_env:
            e["value"] = dynamic_env[e["name"]]
        elif e["name"] in ("S3_ENDPOINT", "S3_BUCKET"):
            e["valueFrom"]["secretKeyRef"]["name"] = cfg.s3_secret

    _set(job, "spec.template.spec.volumes[0].configMap.name", f"campaign-{name}")
    _set(job, "spec.template.spec.volumes[1].configMap.name", f"htr-pipeline-{p.id}")
    _set(
        job,
        "spec.template.spec.volumes[2].persistentVolumeClaim.claimName",
        cfg.data_pvc,
    )
    _set(job, "spec.template.spec.volumes[4].secret.secretName", cfg.s3_secret)

    _set(job, "spec.template.spec.initContainers[0].image", p.image)
    marker = f"/data/warmup/{p.id}.done"
    _set(
        job,
        "spec.template.spec.initContainers[0].command[2]",
        f"until [ -f {marker} ]; do sleep 10; done",
    )

    if cfg.runtime_class:
        _set(job, "spec.template.spec.runtimeClassName", cfg.runtime_class)
    if cfg.node_selector:
        _set(job, "spec.template.spec.nodeSelector", dict(cfg.node_selector))
    if cfg.tolerations:
        _set(
            job,
            "spec.template.spec.tolerations",
            [dict(t) for t in cfg.tolerations],
        )
    return job


def campaign_objects(c: Campaign, p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    parts = split(c.volumes)
    multi = len(parts) > 1
    objects: list[dict] = []
    for i, vols in enumerate(parts, start=1):
        name = f"{c.name}-part{i}" if multi else c.name
        objects.append(_campaign_configmap(name, c, p, vols, cfg))
        objects.append(_campaign_job(name, c, p, vols, cfg))
    return objects
