"""Domain types for campaign/pipeline YAML, with validation (spec §3).

Config-dependent rules (``allowed_image_repos``, ``require_model_revision``,
``source_template``) reach the models through ``ValidationInfo.context`` --
see ``parse.py``, which builds that context once from ``ConverterConfig`` and
passes it to both ``Campaign.model_validate`` and ``Pipeline.model_validate``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

_MiB = 1024 * 1024

_VOLUME_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?\Z")
_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")
_IMAGE_RE = re.compile(r"[a-z0-9./:-]+@sha256:[0-9a-f]{64}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


def _positive_int(v: object) -> bool:
    # `bool` is an `int` in Python: `window: true` is a typo, not a window of 1.
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def _http_url(value: str) -> bool:
    u = urlsplit(value)
    return u.scheme in ("http", "https") and bool(u.netloc)


def _repo_allowed(image: str, allowed: list[str]) -> bool:
    repo = image.split("@", 1)[0]
    return any(
        repo == e or repo.startswith(e + "/")
        for e in (a.strip().rstrip("/") for a in allowed)
        if e
    )


class Volume(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    manifest: str | None = None
    images: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any, info: ValidationInfo) -> Any:
        if isinstance(data, str):
            template = (info.context or {}).get("source_template", "")
            return {"id": data, "manifest": template.format(ref=data)}
        if not isinstance(data, dict) or "id" not in data:
            raise ValueError(f"volume entry needs an id: {data!r}")
        return {**data, "id": str(data["id"])}

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _VOLUME_ID_RE.match(v):
            raise ValueError(f"unsafe volume id: {v!r}")
        return v

    @field_validator("manifest")
    @classmethod
    def _check_manifest(cls, v: str | None) -> str | None:
        if v is not None and not _http_url(v):
            raise ValueError(f"manifest must be an http(s) URL: {v!r}")
        return v

    @field_validator("images")
    @classmethod
    def _check_images(cls, v: list[str]) -> list[str]:
        for u in v:
            if not _http_url(u):
                raise ValueError(f"images must be an http(s) URL: {u!r}")
        return v

    @model_validator(mode="after")
    def _check_source(self) -> "Volume":
        if (self.manifest is not None) == bool(self.images):
            raise ValueError(f"volume {self.id!r} needs manifest or images")
        return self

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
    #: Renders ``spec.suspend``; the apply step enforces it against Kueue.
    suspend: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"unsafe campaign name: {v!r}")
        return v

    @field_validator("pipeline")
    @classmethod
    def _check_pipeline_ref(cls, v: str) -> str:
        if not v:
            raise ValueError("campaign needs pipeline:")
        return v

    @field_validator("window", mode="before")
    @classmethod
    def _check_window(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(f"window must be a positive integer: {v!r}")
        return v


class Pipeline(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    image: str
    steps: list[dict]
    model_revision: str = ""
    #: Per-volume wall-clock budget; overrides converter.yaml's max_seconds.
    max_seconds: int | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"unsafe pipeline id: {v!r}")
        return v

    @field_validator("image")
    @classmethod
    def _check_image(cls, v: str) -> str:
        if not _IMAGE_RE.match(v):
            raise ValueError(f"image must be digest-pinned: {v!r}")
        return v

    @field_validator("steps", mode="before")
    @classmethod
    def _check_steps(cls, v: object) -> object:
        if not isinstance(v, list):
            raise ValueError("missing steps")
        return v

    @field_validator("model_revision")
    @classmethod
    def _check_model_revision(cls, v: str) -> str:
        if v and not _REVISION_RE.match(v):
            raise ValueError(f"model_revision must be 40 hex chars: {v!r}")
        return v

    @field_validator("max_seconds", mode="before")
    @classmethod
    def _check_max_seconds(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(f"max_seconds must be a positive integer: {v!r}")
        return v

    @model_validator(mode="after")
    def _check_repo_allowed(self, info: ValidationInfo) -> "Pipeline":
        allowed = (info.context or {}).get("allowed_image_repos") or []
        if allowed and not _repo_allowed(self.image, allowed):
            raise ValueError(f"image {self.image!r} is not under an allowed repository")
        return self

    @model_validator(mode="after")
    def _check_step_revisions(self, info: ValidationInfo) -> "Pipeline":
        if not (info.context or {}).get("require_model_revision"):
            return self
        problems: list[str] = []
        for step in self.steps:
            if not isinstance(step, dict):
                continue
            settings = step.get("settings") or {}
            ms = settings.get("model_settings") if isinstance(settings, dict) else None
            model = ms.get("model") if isinstance(ms, dict) else None
            if not isinstance(ms, dict) or not model:
                continue
            if not _REVISION_RE.match(str(ms.get("revision") or "")):
                problems.append(
                    f"model {model!r} needs a 40-hex revision (require_model_revision)"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self

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
    window: int = Field(default=20, ge=1)
    s3_secret: str = "htr-batch-s3"
    data_pvc: str = "htr-test-data"
    runtime_class: str = "nvidia"
    node_selector: dict[str, str] = Field(default_factory=dict)
    tolerations: list[dict] = Field(default_factory=list)
    public_results_base: str = ""
    source_template: str = "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
    max_seconds: int = Field(default=21600, ge=1)
    manifest_max_bytes: int = 16 * _MiB
    fetch_max_bytes: int = 64 * _MiB
    allowed_image_repos: list[str] = Field(default_factory=list)
    require_model_revision: bool = False
