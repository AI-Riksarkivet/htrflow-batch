"""ConfigMaps, warm-up Jobs and campaign Indexed Jobs, as dicts (spec §3)."""

from __future__ import annotations

import copy
import re

from .models import Campaign, ConverterConfig, Pipeline, Volume

_LABEL_JUNK = re.compile(r"[^A-Za-z0-9_.-]")
WORKDIR = "/work"
HF_HOME = "/data/hf"
MAX_VOLUMES_PER_JOB = 10_000

_POD_SEC = {
    "runAsNonRoot": True,
    "runAsUser": 1000,
    "runAsGroup": 1000,
    "fsGroup": 1000,
    "seccompProfile": {"type": "RuntimeDefault"},
}
_CTR_SEC = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}

# A real TAB (not `\t\t` as text) between id and source: sed writes one, and
# `${line%%<TAB>*}`/`${line#*<TAB>}` must match it.
_SHELL_ARGS = (
    "set -eu\n"
    'line=$(sed -n "$((JOB_COMPLETION_INDEX + 1))p" /campaign/volumes.txt)\n'
    '[ -n "$line" ] || '
    '{ echo "no volume for index $JOB_COMPLETION_INDEX" >&2; exit 13; }\n'
    "id=${line%%\t*}; src=${line#*\t}\n"
    'export VOLUME_REF="$id"\n'
    'case "$src" in images:*) export IMAGES="${src#images:}" ;; '
    '*) export IIIF_MANIFEST_URL="$src" ;; esac\n'
    "exec python -m htrflow_batch\n"
)


def label_value(text: str) -> str:
    return _LABEL_JUNK.sub("-", text)[:63].strip("-_.")


def split(volumes: list[Volume], size: int = MAX_VOLUMES_PER_JOB) -> list[list[Volume]]:
    if not volumes:
        return [[]]
    return [volumes[i : i + size] for i in range(0, len(volumes), size)]


def _resources(cpu: str, mem_req: str, mem_lim: str, gpu: str | None = None) -> dict:
    req = {"cpu": cpu, "memory": mem_req}
    lim = {"cpu": cpu, "memory": mem_lim}
    if gpu:
        req["nvidia.com/gpu"] = lim["nvidia.com/gpu"] = gpu
    return {"requests": req, "limits": lim}


def _container(
    name: str,
    image: str,
    *,
    mounts: list[dict],
    resources: dict,
    command: list[str] | None = None,
    args: list[str] | None = None,
    env: list[dict] | None = None,
) -> dict:
    c: dict = {"name": name, "image": image}
    if command:
        c["command"] = command
    if args:
        c["args"] = args
    if env is not None:
        c["env"] = env
    c["volumeMounts"] = mounts
    c["securityContext"] = copy.deepcopy(_CTR_SEC)
    c["resources"] = resources
    return c


def _workdir_env() -> list[dict]:
    return [
        {"name": "HF_HOME", "value": HF_HOME},
        {"name": "WORKDIR_PATH", "value": WORKDIR},
        {"name": "HOME", "value": f"{WORKDIR}/home"},
        {"name": "TMPDIR", "value": f"{WORKDIR}/tmp"},
        {"name": "YOLO_CONFIG_DIR", "value": f"{WORKDIR}/ultralytics"},
    ]


def _s3_env(secret: str) -> list[dict]:
    ep = {"secretKeyRef": {"name": secret, "key": "S3_ENDPOINT", "optional": True}}
    bk = {"secretKeyRef": {"name": secret, "key": "S3_BUCKET"}}
    return [
        {"name": "AWS_SHARED_CREDENTIALS_FILE", "value": "/secrets/s3/credentials"},
        {"name": "S3_ENDPOINT", "valueFrom": ep},
        {"name": "S3_BUCKET", "valueFrom": bk},
    ]


def _pod_failure_policy(container_name: str, second_rule_action: str) -> dict:
    return {
        "rules": [
            {"action": "Ignore", "onPodConditions": [{"type": "DisruptionTarget"}]},
            {
                "action": second_rule_action,
                "onExitCodes": {
                    "containerName": container_name,
                    "operator": "In",
                    "values": [13],
                },
            },
        ]
    }


def _pod_template(
    *,
    labels: dict,
    containers: list[dict],
    volumes: list[dict],
    init_containers: list[dict] | None = None,
    runtime_class: str | None = None,
    node_selector: dict[str, str] | None = None,
    tolerations: list[dict] | None = None,
) -> dict:
    spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": copy.deepcopy(_POD_SEC),
        "containers": containers,
        "volumes": volumes,
    }
    if init_containers:
        spec["initContainers"] = init_containers
    if runtime_class:
        spec["runtimeClassName"] = runtime_class
    if node_selector:
        spec["nodeSelector"] = dict(node_selector)
    if tolerations:
        spec["tolerations"] = [dict(t) for t in tolerations]
    return {"metadata": {"labels": labels}, "spec": spec}


def _job(
    name: str, namespace: str, labels: dict, spec: dict, annotations: dict | None = None
) -> dict:
    meta = {"name": name, "namespace": namespace, "labels": labels}
    if annotations:
        meta["annotations"] = annotations
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": meta, "spec": spec}


def _configmap(
    name: str,
    namespace: str,
    data: dict,
    labels: dict,
    annotations: dict | None = None,
) -> dict:
    # The labels are not decoration: `kubectl apply --prune -l
    # htrflow.riksarkivet.se/managed-by=converter` (and Argo CD's own prune)
    # find a deleted campaign's leftovers by them. An unlabelled ConfigMap
    # would outlive the Job it fed.
    meta = {"name": name, "namespace": namespace, "labels": labels}
    if annotations:
        meta["annotations"] = annotations
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": data}


def _warmup_job(p: Pipeline, cfg: ConverterConfig) -> dict:
    env = [
        {"name": "PIPELINE_PATH", "value": "/config/pipeline.yaml"},
        {"name": "PIPELINE_ID", "value": p.id},
        {"name": "CUDA_VISIBLE_DEVICES", "value": ""},
        *_workdir_env(),
    ]
    container = _container(
        "warmup",
        p.image,
        command=["python", "-m", "htrflow_batch.warmup"],
        env=env,
        mounts=[
            {"name": "pipeline", "mountPath": "/config"},
            {"name": "data", "mountPath": "/data"},
            {"name": "work", "mountPath": WORKDIR},
        ],
        resources=_resources("2", "4Gi", "8Gi"),
    )
    volumes = [
        {"name": "pipeline", "configMap": {"name": f"htr-pipeline-{p.id}"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": cfg.data_pvc}},
        {"name": "work", "emptyDir": {"sizeLimit": "4Gi"}},
    ]
    spec = {
        "backoffLimit": 2,
        "podFailurePolicy": _pod_failure_policy("warmup", "FailJob"),
        "activeDeadlineSeconds": 3600,
        "template": _pod_template(
            labels={"app": "htrflow-warmup"}, containers=[container], volumes=volumes
        ),
    }
    labels = {
        "app": "htrflow-warmup",
        "htrflow.riksarkivet.se/managed-by": "converter",
        "htrflow.riksarkivet.se/pipeline": label_value(p.id),
    }
    return _job(f"htr-warmup-{p.id}", cfg.namespace, labels, spec)


def pipeline_objects(p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    cm = _configmap(
        f"htr-pipeline-{p.id}",
        cfg.namespace,
        {"pipeline.yaml": p.pipeline_yaml()},
        {
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/pipeline": label_value(p.id),
        },
        {"htrflow.riksarkivet.se/pipeline-sha256": p.sha256},
    )
    return [cm, _warmup_job(p, cfg)]


def _warmup_wait_container(image: str, pipeline_id: str) -> dict:
    marker = f"/data/warmup/{pipeline_id}.done"
    return _container(
        "warmup-wait",
        image,
        command=["/bin/sh", "-c", f"until [ -f {marker} ]; do sleep 10; done"],
        mounts=[{"name": "data", "mountPath": "/data", "readOnly": True}],
        resources=_resources("50m", "64Mi", "64Mi"),
    )


def _wrapper_container(p: Pipeline, cfg: ConverterConfig) -> dict:
    prefix = "" if cfg.legacy_layout else f"{cfg.namespace}/"
    env = [
        {"name": "PIPELINE_PATH", "value": "/config/pipeline.yaml"},
        {"name": "PIPELINE_ID", "value": p.id},
        {"name": "S3_PREFIX", "value": prefix},
        {"name": "PUBLIC_RESULTS_BASE", "value": cfg.public_results_base},
        {"name": "IMAGE_DIGEST", "value": p.image},
        {"name": "HF_HUB_OFFLINE", "value": "1"},
        {"name": "MAX_SECONDS", "value": str(p.max_seconds or cfg.max_seconds)},
        {"name": "MANIFEST_MAX_BYTES", "value": str(cfg.manifest_max_bytes)},
        {"name": "FETCH_MAX_BYTES", "value": str(cfg.fetch_max_bytes)},
        *_workdir_env(),
        *_s3_env(cfg.s3_secret),
    ]
    return _container(
        "wrapper",
        p.image,
        command=["/bin/sh", "-c"],
        args=[_SHELL_ARGS],
        env=env,
        mounts=[
            {"name": "campaign", "mountPath": "/campaign", "readOnly": True},
            {"name": "pipeline", "mountPath": "/config"},
            {"name": "data", "mountPath": "/data", "readOnly": True},
            {"name": "work", "mountPath": WORKDIR},
            {"name": "s3", "mountPath": "/secrets/s3", "readOnly": True},
        ],
        resources=_resources("4", "8Gi", "16Gi", gpu="1"),
    )


def _campaign_configmap(
    name: str, c: Campaign, p: Pipeline, volumes: list[Volume], cfg: ConverterConfig
) -> dict:
    text = "\n".join(v.source_line() for v in volumes) + "\n" if volumes else ""
    return _configmap(
        f"campaign-{name}",
        cfg.namespace,
        {"volumes.txt": text},
        {
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/campaign": label_value(c.name),
            "htrflow.riksarkivet.se/pipeline": label_value(p.id),
        },
    )


def _campaign_job(
    name: str, c: Campaign, p: Pipeline, volumes: list[Volume], cfg: ConverterConfig
) -> dict:
    completions = len(volumes)
    # cfg.window is the per-cluster CAP: a campaign may ask for less, never
    # more. Kueue partial admission would shrink an oversized parallelism on
    # the live Job instead -- and then reject every later apply of the
    # unchanged rendered file (docs: development/e2e-indexed-jobs.md).
    parallelism = min(c.window or cfg.window, cfg.window)
    labels = {
        "app": "htrflow-batch",
        "htrflow.riksarkivet.se/managed-by": "converter",
        "htrflow.riksarkivet.se/campaign": label_value(c.name),
        "htrflow.riksarkivet.se/pipeline": label_value(p.id),
        "kueue.x-k8s.io/queue-name": cfg.queue,
    }
    if c.priority:
        labels["kueue.x-k8s.io/priority-class"] = c.priority
    volumes_spec = [
        {"name": "campaign", "configMap": {"name": f"campaign-{name}"}},
        {"name": "pipeline", "configMap": {"name": f"htr-pipeline-{p.id}"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": cfg.data_pvc}},
        {"name": "work", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        {"name": "s3", "secret": {"secretName": cfg.s3_secret, "defaultMode": 0o440}},
    ]
    spec = {
        "completionMode": "Indexed",
        "completions": completions,
        "parallelism": parallelism,
        "backoffLimitPerIndex": 3,
        "maxFailedIndexes": completions,
        "podFailurePolicy": _pod_failure_policy("wrapper", "FailIndex"),
        "ttlSecondsAfterFinished": 86400,
        "template": _pod_template(
            labels={"app": "htrflow-batch"},
            containers=[_wrapper_container(p, cfg)],
            init_containers=[_warmup_wait_container(p.image, p.id)],
            volumes=volumes_spec,
            runtime_class=cfg.runtime_class,
            node_selector=cfg.node_selector,
            tolerations=cfg.tolerations,
        ),
    }
    # ``name`` is the Job's own metadata.name (and the campaign ConfigMap's
    # name suffix): a K8s object name is a DNS-1123 *subdomain* (<=253 chars,
    # no per-63-char label truncation), unlike the label VALUES above, which
    # go through label_value(). Truncating this too would make "-part1" and
    # "-part10" collide once c.name is close to 63 chars.
    return _job(name, cfg.namespace, labels, spec)


def campaign_objects(c: Campaign, p: Pipeline, cfg: ConverterConfig) -> list[dict]:
    parts = split(c.volumes)
    multi = len(parts) > 1
    objects: list[dict] = []
    for i, vols in enumerate(parts, start=1):
        name = f"{c.name}-part{i}" if multi else c.name
        objects.append(_campaign_configmap(name, c, p, vols, cfg))
        objects.append(_campaign_job(name, c, p, vols, cfg))
    return objects
