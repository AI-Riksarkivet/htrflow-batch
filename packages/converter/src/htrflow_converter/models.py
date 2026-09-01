"""Domain types for campaign/pipeline YAML (spec §3)."""

from __future__ import annotations

import hashlib

import yaml
from pydantic import BaseModel, ConfigDict, Field

_MiB = 1024 * 1024


class Volume(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    manifest: str | None = None
    images: list[str] = Field(default_factory=list)

    def source_line(self) -> str:
        """One line of a campaign's ``volumes.txt`` ConfigMap."""
        if self.manifest is not None:
            return f"{self.id}\t{self.manifest}"
        return f"{self.id}\timages:{','.join(self.images)}"


class Campaign(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    pipeline: str
    volumes: list[Volume] = Field(default_factory=list)
    priority: str = ""
    window: int | None = None


class Pipeline(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    image: str
    steps: list[dict] = Field(default_factory=list)
    model_revision: str = ""

    def pipeline_yaml(self) -> str:
        return yaml.safe_dump({"steps": self.steps}, sort_keys=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pipeline_yaml().encode()).hexdigest()


class ConverterConfig(BaseModel):
    """``converter.yaml`` in the campaigns repo; unknown keys rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str = "htr-batch"
    queue: str = "htr-batch"
    window: int = 20
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    runtime_class: str = "nvidia"
    node_selector: dict[str, str] = Field(default_factory=dict)
    tolerations: list[dict] = Field(default_factory=list)
    public_results_base: str = ""
    legacy_layout: bool = False
    source_template: str = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
    max_seconds: int = 21600
    manifest_max_bytes: int = 16 * _MiB
    fetch_max_bytes: int = 64 * _MiB
    allowed_image_repos: list[str] = Field(default_factory=list)
    require_model_revision: bool = False
