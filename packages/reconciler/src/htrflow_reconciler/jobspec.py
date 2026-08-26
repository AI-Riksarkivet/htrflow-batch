"""Job dicts for campaign volumes — same shape as the proven hand-run jobs
(R0001203, loc-mal2459400), image + IMAGE_DIGEST from the pipeline pin.

Pod hardening (docs: development/security, D14): every pod built here meets
Pod Security ``restricted`` — non-root, no capabilities, read-only root
filesystem, RuntimeDefault seccomp, no ServiceAccount token. Batch Jobs read
the model cache read-only and offline; the per-pipeline warm-up Job is the
one writer, and the only pod allowed to reach HF Hub (NetworkPolicy in the
chart keys off the ``app`` label).
"""

from pydantic import BaseModel, ConfigDict

from .models import PipelineSpec, Volume
from .status import job_name

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
    active_deadline_seconds: int = 21600
    #: STALE threshold advertised in status.json; must match the CronJob
    #: schedule (RECONCILER_TICK_SECONDS, audit R12).
    tick_seconds: int = 300
    #: Upper bound on one tick, mirrored by the CronJob's activeDeadlineSeconds
    #: (audit O7); also the Lease duration.
    tick_deadline_seconds: int = 600
    #: coordination.k8s.io Lease taken per tick (audit O8): a manual
    #: ``kubectl create job --from=cronjob`` bypasses concurrencyPolicy.
    lease_name: str = "htr-reconciler"
    #: Pre-validation is O(new volumes) per tick, never O(volumes) (audit X1).
    max_validations_per_tick: int = 50
    #: An unreachable manifest is not re-probed for this many ticks: a dead
    #: host must not cost every tick a timeout (audit X1/S5).
    unreachable_ticks: int = 3


def warmup_job_name(pipeline_id: str) -> str:
    """Deterministic warm-up Job name; the chart renders the same name for
    ``values.pipelines`` so Helm and the reconciler never race on a pipeline."""
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
    return {"metadata": {"labels": labels}, "spec": spec}


def build_job(
    pipeline: PipelineSpec,
    volume: Volume,
    manifest_url: str,
    cfg: ReconcilerConfig,
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
                "kueue.x-k8s.io/queue-name": cfg.queue,
            },
        },
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "activeDeadlineSeconds": cfg.active_deadline_seconds,
            "ttlSecondsAfterFinished": 86400,
            "template": _pod_template(
                labels={"app": "htrflow-batch"},
                container=container,
                volumes=volumes,
                runtime_class="nvidia",
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
            "activeDeadlineSeconds": 3600,
            "template": _pod_template(
                labels={"app": "htrflow-warmup"},
                container=container,
                volumes=volumes,
                runtime_class=None,
            ),
        },
    }
