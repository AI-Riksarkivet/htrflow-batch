"""Job dicts for campaign volumes — same shape as the proven hand-run jobs
(R0001203, loc-mal2459400), image + IMAGE_DIGEST from the pipeline pin.

Pod hardening (docs: development/security, D14): every pod built here meets
Pod Security ``restricted`` — non-root, no capabilities, read-only root
filesystem, RuntimeDefault seccomp, no ServiceAccount token. Batch Jobs read
the model cache read-only and offline; the per-pipeline warm-up Job is the
one writer, and the only pod allowed to reach HF Hub (NetworkPolicy in the
chart keys off the ``app`` label).
"""

import re

from pydantic import BaseModel, ConfigDict

from .models import PipelineSpec, Volume
from .status import job_name

_LABEL_JUNK = re.compile(r"[^A-Za-z0-9_.-]")


def label_value(text: str) -> str:
    """A Kubernetes label value from free text (campaign file stems): the
    allowed alphabet, alphanumeric at both ends, at most 63 chars."""
    value = _LABEL_JUNK.sub("-", text)[:63]
    return value.strip("-_.")


#: uid/gid the images run as (``USER 1000`` in both dockerfiles). Repeated
#: here so a pod spec cannot silently regress to root if an image forgets it.
RUN_AS = 1000

#: The tmpfs workdir is the only writable mount besides the warm-up's cache.
#: Everything htrflow's stack writes outside HF_HOME — ultralytics settings,
#: triton/inductor JIT caches (under HOME), temp files — is pointed here.
WORKDIR = "/work"
HF_HOME = "/data/hf"

_POD_SECURITY = {
    "runAsNonRoot": True,
    "runAsUser": RUN_AS,
    "runAsGroup": RUN_AS,
    "fsGroup": RUN_AS,
    "seccompProfile": {"type": "RuntimeDefault"},
}
_CONTAINER_SECURITY = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}


class ReconcilerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    public_results_base: str
    # Job-facing base for synthetic manifests (in-cluster S3 endpoint); empty
    # falls back to public_results_base (real AWS reaches everywhere).
    internal_results_base: str = ""
    campaigns_repo_url: str = ""
    campaigns_repo_web_url: str = ""
    namespace: str = "htr-batch"
    queue: str = "htr-batch"
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    window: int = 20
    attempt_cap: int = 3
    #: Job ``activeDeadlineSeconds = max(min, pages x per_page)`` (audit O2):
    #: ~13 s/page measured on the GB10, so a flat 6 h cannot finish a long
    #: volume; the minimum applies when the page count is unknown.
    job_min_deadline_seconds: int = 21600
    job_seconds_per_page: int = 30
    #: The wrapper's own fetch caps (A2 contract), passed through as env.
    job_manifest_max_bytes: int = 16 * 1024 * 1024
    job_fetch_max_bytes: int = 64 * 1024 * 1024
    #: GPU placement (audit O15): empty runtime class omits the field.
    job_runtime_class: str = "nvidia"
    job_node_selector: dict[str, str] = {}
    job_tolerations: list[dict] = []
    #: STALE threshold advertised in status.json; must match the CronJob
    #: schedule (RECONCILER_TICK_SECONDS, audit R12).
    tick_seconds: int = 300
    #: Upper bound on one tick, mirrored by the CronJob's activeDeadlineSeconds
    #: (audit O7); also the Lease duration.
    tick_deadline_seconds: int = 600
    #: coordination.k8s.io Lease taken per tick (audit O8): a manual
    #: ``kubectl create job --from=cronjob`` bypasses concurrencyPolicy.
    lease_name: str = "htr-reconciler"
    #: Image repositories a pipeline may pin (prefix match on a path boundary,
    #: before ``@sha256:``). Empty admits any digest-pinned image, with a
    #: warning in status.json (audit S1).
    allowed_image_repos: tuple[str, ...] = ()
    #: Every ``model_settings.model`` must carry a 40-hex ``revision``.
    require_model_revision: bool = False
    #: Pre-validation is O(new volumes) per tick, never O(volumes) (audit X1).
    max_validations_per_tick: int = 50
    #: An unreachable manifest is not re-probed for this many ticks: a dead
    #: host must not cost every tick a timeout (audit X1/S5).
    unreachable_ticks: int = 3


def warmup_job_name(pipeline_id: str) -> str:
    """Deterministic warm-up Job name. The manual path for chart-declared
    pipelines (``make warmup`` -> ``htrflow_reconciler.warmup``) uses the
    same function, so the two never race on a pipeline; the chart itself
    renders no warm-up Job."""
    return f"htr-warmup-{pipeline_id}"


def _workdir_env() -> list[dict]:
    return [
        {"name": "HF_HOME", "value": HF_HOME},
        {"name": "WORKDIR_PATH", "value": WORKDIR},
        {"name": "HOME", "value": f"{WORKDIR}/home"},
        {"name": "TMPDIR", "value": f"{WORKDIR}/tmp"},
        {"name": "YOLO_CONFIG_DIR", "value": f"{WORKDIR}/ultralytics"},
    ]


def _s3_env(secret: str) -> list[dict]:
    """Credentials reach boto3 as a file (``AWS_SHARED_CREDENTIALS_FILE``),
    never as env: env leaks through ``kubectl describe``, crash dumps and every
    child process. Endpoint and bucket are not secret and stay plain env, read
    off the same Secret so one object still configures S3 end to end."""
    return [
        {"name": "AWS_SHARED_CREDENTIALS_FILE", "value": "/secrets/s3/credentials"},
        {
            "name": "S3_ENDPOINT",
            "valueFrom": {
                # Absent for real AWS (provider default chain).
                "secretKeyRef": {"name": secret, "key": "S3_ENDPOINT", "optional": True}
            },
        },
        {
            "name": "S3_BUCKET",
            "valueFrom": {"secretKeyRef": {"name": secret, "key": "S3_BUCKET"}},
        },
    ]


def _pod_template(
    *,
    labels: dict,
    container: dict,
    volumes: list[dict],
    runtime_class: str | None,
    node_selector: dict[str, str] | None = None,
    tolerations: list[dict] | None = None,
) -> dict:
    spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": dict(_POD_SECURITY),
        "containers": [{**container, "securityContext": dict(_CONTAINER_SECURITY)}],
        "volumes": volumes,
    }
    if runtime_class:
        spec["runtimeClassName"] = runtime_class
    if node_selector:
        spec["nodeSelector"] = dict(node_selector)
    if tolerations:
        spec["tolerations"] = [dict(t) for t in tolerations]
    return {"metadata": {"labels": labels}, "spec": spec}


def _pod_failure_policy(container_name: str) -> dict:
    """The contract the docs describe (audit O2/O3): a disruption (drain,
    preemption) does not fail the Job — with ``backoffLimit: 0`` the pod is
    simply replaced — and exit 13 fails it at once, so the Failed condition
    carries reason ``PodFailurePolicy`` even after the pod is reaped (R6)."""
    return {
        "rules": [
            {"action": "Ignore", "onPodConditions": [{"type": "DisruptionTarget"}]},
            {
                "action": "FailJob",
                "onExitCodes": {
                    "containerName": container_name,
                    "operator": "In",
                    "values": [13],
                },
            },
        ]
    }


def deadline_seconds(cfg: ReconcilerConfig, page_count: int | None) -> int:
    if not page_count:
        return cfg.job_min_deadline_seconds
    return max(cfg.job_min_deadline_seconds, page_count * cfg.job_seconds_per_page)


def build_job(
    pipeline: PipelineSpec,
    volume: Volume,
    manifest_url: str,
    cfg: ReconcilerConfig,
    *,
    campaign: str = "",
    page_count: int | None = None,
) -> dict:
    name = job_name(pipeline.id, volume.id)
    env = [
        {"name": "VOLUME_REF", "value": volume.id},
        {"name": "IIIF_MANIFEST_URL", "value": manifest_url},
        {"name": "PIPELINE_PATH", "value": "/config/pipeline.yaml"},
        {"name": "PIPELINE_ID", "value": pipeline.id},
        # Explicit env beats a secret-wide import: pinning it empty keeps
        # results at <pipeline>/<volume>/… where done-detection looks, even if
        # the S3 secret carries an S3_PREFIX of its own.
        {"name": "S3_PREFIX", "value": ""},
        {"name": "PUBLIC_RESULTS_BASE", "value": cfg.public_results_base},
        {"name": "IMAGE_DIGEST", "value": pipeline.image},
        # The cache is warmed by the warm-up Job; a batch Job never downloads.
        {"name": "HF_HUB_OFFLINE", "value": "1"},
        # Byte caps on the wrapper's manifest and image fetches (S5).
        {"name": "MANIFEST_MAX_BYTES", "value": str(cfg.job_manifest_max_bytes)},
        {"name": "FETCH_MAX_BYTES", "value": str(cfg.job_fetch_max_bytes)},
        *_workdir_env(),
        *_s3_env(cfg.s3_secret),
    ]
    container = {
        "name": "wrapper",
        "image": pipeline.image,
        "env": env,
        "volumeMounts": [
            {"name": "pipeline", "mountPath": "/config"},
            {"name": "data", "mountPath": "/data", "readOnly": True},
            {"name": "work", "mountPath": WORKDIR},
            {"name": "s3", "mountPath": "/secrets/s3", "readOnly": True},
        ],
        "resources": {
            "requests": {"cpu": "4", "memory": "8Gi", "nvidia.com/gpu": "1"},
            "limits": {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
        },
    }
    volumes = [
        {"name": "pipeline", "configMap": {"name": f"htr-pipeline-{pipeline.id}"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": cfg.data_pvc}},
        {"name": "work", "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"}},
        {"name": "s3", "secret": {"secretName": cfg.s3_secret, "defaultMode": 0o440}},
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": cfg.namespace,
            "labels": {
                # ``app`` is what the operators' selectors and the hand-run Jobs
                # share; ``managed-by`` is what tells the reconciler's own Jobs
                # apart from those, so only these count against the window.
                "app": "htrflow-batch",
                "batch.htrflow/managed-by": "reconciler",
                "batch.htrflow/volume": volume.id.lower(),
                "batch.htrflow/pipeline": pipeline.id.lower(),
                # What the fairness order counts per campaign (audit R5).
                "batch.htrflow/campaign": label_value(campaign),
                "kueue.x-k8s.io/queue-name": cfg.queue,
            },
        },
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "podFailurePolicy": _pod_failure_policy("wrapper"),
            "activeDeadlineSeconds": deadline_seconds(cfg, page_count),
            "ttlSecondsAfterFinished": 86400,
            "template": _pod_template(
                labels={"app": "htrflow-batch"},
                container=container,
                volumes=volumes,
                runtime_class=cfg.job_runtime_class,
                node_selector=cfg.job_node_selector,
                tolerations=cfg.job_tolerations,
            ),
        },
    }


def build_warmup_job(pipeline: PipelineSpec, cfg: ReconcilerConfig) -> dict:
    """The one pod that fills the model cache for ``pipeline``.

    Same image and pipeline ConfigMap as the batch Jobs, so it downloads
    exactly what ``Pipeline.from_config()`` will load — no second parser of
    the pipeline YAML. CPU-only (models are instantiated, not run), outside
    the Kueue queue (no GPU to gate), and never TTL-reaped: its ``Complete``
    condition is what gates the pipeline's volumes.
    """
    env = [
        {"name": "PIPELINE_PATH", "value": "/config/pipeline.yaml"},
        {"name": "PIPELINE_ID", "value": pipeline.id},
        {"name": "CUDA_VISIBLE_DEVICES", "value": ""},
        *_workdir_env(),
    ]
    container = {
        "name": "warmup",
        "image": pipeline.image,
        "command": ["python", "-m", "htrflow_batch.warmup"],
        "env": env,
        "volumeMounts": [
            {"name": "pipeline", "mountPath": "/config"},
            {"name": "data", "mountPath": "/data"},
            {"name": "work", "mountPath": WORKDIR},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {"cpu": "2", "memory": "8Gi"},
        },
    }
    volumes = [
        {"name": "pipeline", "configMap": {"name": f"htr-pipeline-{pipeline.id}"}},
        {"name": "data", "persistentVolumeClaim": {"claimName": cfg.data_pvc}},
        # Disk, not memory: nothing here is hot-path, and model instantiation
        # on CPU already needs the RAM.
        {"name": "work", "emptyDir": {"sizeLimit": "4Gi"}},
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": warmup_job_name(pipeline.id),
            "namespace": cfg.namespace,
            "labels": {
                "app": "htrflow-warmup",
                "batch.htrflow/managed-by": "reconciler",
                "batch.htrflow/pipeline": pipeline.id.lower(),
            },
        },
        "spec": {
            "backoffLimit": 2,
            "podFailurePolicy": _pod_failure_policy("warmup"),
            "activeDeadlineSeconds": 3600,
            "template": _pod_template(
                labels={"app": "htrflow-warmup"},
                container=container,
                volumes=volumes,
                runtime_class=None,
            ),
        },
    }
