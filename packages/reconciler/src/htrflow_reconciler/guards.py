"""D17 immutability guards: a pipeline id names one recipe, forever
(spec §3). The S3 ground-truth check is the one that protects results."""

from __future__ import annotations

from .models import PipelineSpec


def check_drift(
    pipeline: PipelineSpec,
    configmap_steps: str | None,
    published: dict | None,
) -> tuple[bool, str | None]:
    if configmap_steps is not None and configmap_steps != pipeline.steps_yaml:
        return False, f"pipeline {pipeline.id}: ConfigMap drift — refusing to submit"
    if published is not None:
        if published.get("pipeline_sha256") != pipeline.steps_sha256:
            return False, f"pipeline {pipeline.id}: steps differ from published results"
        digest = published.get("image_digest")
        if digest == "unknown":
            return True, (
                f"pipeline {pipeline.id}: published results predate image "
                "pinning (image_digest unknown) — grandfathered"
            )
        if digest != pipeline.image:
            return (
                False,
                f"pipeline {pipeline.id}: image differs from published results",
            )
    return True, None
