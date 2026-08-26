"""D17 immutability guards: a pipeline id names one recipe, forever
(spec §3). The S3 ground-truth check is the one that protects results."""

from __future__ import annotations

import yaml

from .models import PipelineSpec
from .parse import canonical_sha256


def _same_recipe(configmap_steps: str, pipeline: PipelineSpec) -> bool:
    """Compared as parsed content, not text: the ConfigMap may have been
    written by another PyYAML version (key order, flow style, quoting)."""
    if configmap_steps == pipeline.steps_yaml:
        return True
    try:
        doc = yaml.safe_load(configmap_steps)
    except yaml.YAMLError:
        return False
    return canonical_sha256(doc) == pipeline.steps_sha256


def check_drift(
    pipeline: PipelineSpec,
    configmap_steps: str | None,
    published: dict | None,
) -> tuple[bool, str | None]:
    if configmap_steps is not None and not _same_recipe(configmap_steps, pipeline):
        return False, f"pipeline {pipeline.id}: ConfigMap drift — refusing to submit"
    if published is not None:
        sha = published.get("pipeline_sha256")
        if not sha:
            # A manifest that cannot testify must not block forever (R11).
            return True, (
                f"pipeline {pipeline.id}: published results carry no provenance "
                "(pipeline_sha256) — drift check skipped"
            )
        # The wrapper hashes the ConfigMap text it was given; results published
        # before the canonical hash (and by wrappers that still hash the text)
        # match the legacy sha (R10).
        if sha not in (pipeline.steps_sha256, pipeline.legacy_sha256):
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
