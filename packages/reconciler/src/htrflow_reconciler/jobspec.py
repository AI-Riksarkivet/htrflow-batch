"""Job dicts for campaign volumes — same shape as the proven hand-run jobs
(R0001203, loc-mal2459400), image + IMAGE_DIGEST from the pipeline pin."""

from pydantic import BaseModel, ConfigDict

from .models import PipelineSpec, Volume
from .status import job_name


class ReconcilerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    public_results_base: str
    namespace: str = "htr-batch"
    queue: str = "htr-batch"
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    window: int = 20
    attempt_cap: int = 3
    active_deadline_seconds: int = 21600


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
        # Explicit env beats envFrom: pinning it empty keeps results at
        # <pipeline>/<volume>/… where done-detection looks, even if the S3
        # secret carries an S3_PREFIX of its own.
        {"name": "S3_PREFIX", "value": ""},
        {"name": "PUBLIC_RESULTS_BASE", "value": cfg.public_results_base},
        {"name": "IMAGE_DIGEST", "value": pipeline.image},
        {"name": "HF_HOME", "value": "/data/hf"},
        {"name": "WORKDIR_PATH", "value": "/work"},
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
            "template": {
                "metadata": {"labels": {"app": "htrflow-batch"}},
                "spec": {
                    "restartPolicy": "Never",
                    "runtimeClassName": "nvidia",
                    "containers": [
                        {
                            "name": "wrapper",
                            "image": pipeline.image,
                            "env": env,
                            "envFrom": [{"secretRef": {"name": cfg.s3_secret}}],
                            "volumeMounts": [
                                {"name": "pipeline", "mountPath": "/config"},
                                {"name": "data", "mountPath": "/data"},
                                {"name": "work", "mountPath": "/work"},
                            ],
                            "resources": {
                                "requests": {
                                    "cpu": "4",
                                    "memory": "8Gi",
                                    "nvidia.com/gpu": "1",
                                },
                                "limits": {
                                    "cpu": "4",
                                    "memory": "16Gi",
                                    "nvidia.com/gpu": "1",
                                },
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "pipeline",
                            "configMap": {"name": f"htr-pipeline-{pipeline.id}"},
                        },
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": cfg.data_pvc},
                        },
                        {
                            "name": "work",
                            "emptyDir": {"medium": "Memory", "sizeLimit": "2Gi"},
                        },
                    ],
                },
            },
        },
    }
