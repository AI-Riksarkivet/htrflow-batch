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

#: `name`/`id` are taken from the file name (parse.py overrides whatever the
#: YAML says), so the only way to fix either is to rename the file.
_RENAME_THE_FILE = (
    "cannot be a Kubernetes object name — rename the file to lower-case "
    'letters, digits, "." and "-" only, at most 63 characters'
)


def shown(value: object) -> str:
    """A value echoed back the way the author wrote it. A number in quotes is
    the mistake behind half of these messages -- YAML then hands us text -- so
    say so, rather than leave them to spot ``5`` against ``"5"``. Only for a
    value that IS a number, though: telling the author of ``suspend: maybe``
    that quotes made it text describes a file they did not write."""
    if isinstance(value, (list, tuple)):
        return "a list"  # never str() -- that is a Python repr, with its quotes
    if isinstance(value, dict):
        return "a block of settings"
    if not isinstance(value, str):
        return str(value).lower()
    digits = value.strip().lstrip("-+").isdigit()
    return f'"{value}"' + (" — quotes make it text" if digits else "")


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
            raise ValueError(
                'has no id — write the entry as "- R1", or as "- id: R1" '
                "with manifest: or images:"
            )
        return {**data, "id": str(data["id"])}

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _VOLUME_ID_RE.match(v):
            raise ValueError(
                "has an id with characters that are not allowed — use only "
                'letters, digits, ".", "_" and "-", at most 63 of them'
            )
        return v

    @field_validator("manifest")
    @classmethod
    def _check_manifest(cls, v: str | None) -> str | None:
        if v is not None and not _http_url(v):
            raise ValueError(
                f'has a manifest that is not an http(s) URL ("{v}") — write '
                "the whole URL, starting with https://"
            )
        return v

    @field_validator("images")
    @classmethod
    def _check_images(cls, v: list[str]) -> list[str]:
        for u in v:
            if not _http_url(u):
                raise ValueError(
                    f'lists an image that is not an http(s) URL ("{u}") — '
                    "every entry under images: is a whole URL"
                )
        return v

    @model_validator(mode="after")
    def _check_source(self) -> "Volume":
        if (self.manifest is not None) == bool(self.images):
            raise ValueError(
                "needs exactly one source — give it manifest: <IIIF manifest "
                "URL>, or images: <list of image URLs>"
            )
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
            raise ValueError(_RENAME_THE_FILE)
        return v

    @field_validator("pipeline")
    @classmethod
    def _check_pipeline_ref(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "is empty — name one of the files in pipelines/, without the .yaml"
            )
        return v

    @field_validator("window", mode="before")
    @classmethod
    def _check_window(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(f"must be a whole number of 1 or more (got {shown(v)})")
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
            raise ValueError(_RENAME_THE_FILE)
        return v

    @field_validator("image")
    @classmethod
    def _check_image(cls, v: str) -> str:
        if not _IMAGE_RE.match(v):
            raise ValueError(
                f'is not pinned to a digest (got "{v}") — write image: '
                "<registry>/<repo>@sha256:<64 hex digits>"
            )
        return v

    @field_validator("steps", mode="before")
    @classmethod
    def _check_steps(cls, v: object) -> object:
        if not isinstance(v, list):
            raise ValueError(
                'must be a list of steps — write steps: and then "- step: '
                '<Name>" entries under it'
            )
        return v

    @field_validator("model_revision")
    @classmethod
    def _check_model_revision(cls, v: str) -> str:
        if v and not _REVISION_RE.match(v):
            raise ValueError(
                f"must be a 40-character commit hash (got {shown(v)}) — copy "
                "it from the model's page on Hugging Face"
            )
        return v

    @field_validator("max_seconds", mode="before")
    @classmethod
    def _check_max_seconds(cls, v: object) -> object:
        if v is not None and not _positive_int(v):
            raise ValueError(
                f"must be a whole number of seconds, 1 or more (got {shown(v)})"
            )
        return v

    @model_validator(mode="after")
    def _check_repo_allowed(self, info: ValidationInfo) -> "Pipeline":
        allowed = (info.context or {}).get("allowed_image_repos") or []
        if allowed and not _repo_allowed(self.image, allowed):
            raise ValueError(
                f'the image is not from an allowed repository ("{self.image}")'
                f" — converter.yaml allows only: {', '.join(allowed)}"
            )
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
                    f'the model "{model}" is not pinned to a revision — '
                    "converter.yaml sets require_model_revision, so add "
                    "revision: <40-character commit hash>"
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
