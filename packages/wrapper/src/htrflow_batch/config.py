"""Env contract for the wrapper (docs: wrapper).

**No wrapper setting is ever a secret.** S3 credentials reach the pod as a
mounted Secret file (``AWS_SHARED_CREDENTIALS_FILE=/secrets/s3/credentials``),
never as an env var — an env var is readable in `kubectl describe`, in a crash
dump and in every child process. ``test_config.py`` asserts no name below
matches ``KEY|TOKEN|PASSWORD|SECRET_ACCESS``.

One idiom across the workspace (B63 Task 27): a frozen pydantic model whose
fields carry their own source name and one ``from_env``/``from_yaml``
classmethod. The env names here are bare (``S3_BUCKET``, not
``HTRFLOW_S3_BUCKET``) because they are an in-pod contract written by the
rendered Job, not an operator's settings — the web front, which is
operator-facing, uses ``HTRFLOW_``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class ConfigError(ValueError):
    pass


#: What may appear in a name the wrapper builds S3 keys and public URLs out of
#: (W12, 2026-09-14 audit).
_KEY_SAFE = re.compile(r"[A-Za-z0-9._-]+")


class Config(BaseModel):
    """Every field is read from its ``alias`` and from nowhere else, so there
    is no second table to keep in step: a field without a default is required,
    and the class-level default is the single source of truth for every
    default. The raw string goes to pydantic, which coerces it ("64" -> int,
    "off"/"true" -> bool) and raises ValidationError -- a ValueError, so _main
    classifies it PERMANENT (exit 13) -- for one it cannot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    volume_ref: str = Field(alias="VOLUME_REF")
    pipeline_path: str = Field(alias="PIPELINE_PATH")
    pipeline_id: str = Field(alias="PIPELINE_ID")
    s3_endpoint: str = Field("", alias="S3_ENDPOINT")
    s3_bucket: str = Field(alias="S3_BUCKET")
    public_results_base: str = Field(alias="PUBLIC_RESULTS_BASE")
    # Exactly one of these is set (docs: wrapper, IMAGES) — see from_env.
    manifest_url: str = Field("", alias="IIIF_MANIFEST_URL")
    images: str = Field("", alias="IMAGES")
    s3_prefix: str = Field("", alias="S3_PREFIX")
    max_image_width: int = Field(2500, alias="MAX_IMAGE_WIDTH")
    resume: bool = Field(True, alias="RESUME")
    lookahead_pages: int = Field(64, alias="LOOKAHEAD_PAGES")
    max_pages: int = Field(0, alias="MAX_PAGES")
    workdir: str = Field("/work", alias="WORKDIR_PATH")
    download_concurrency: int = Field(12, alias="DOWNLOAD_CONCURRENCY")
    # 0 disables live shipping of the run log
    log_ship_seconds: float = Field(15.0, alias="LOG_SHIP_SECONDS")
    # S5 byte caps on fetches driven by campaign data (docs: wrapper)
    manifest_max_bytes: int = Field(16 * 1024 * 1024, alias="MANIFEST_MAX_BYTES")
    fetch_max_bytes: int = Field(64 * 1024 * 1024, alias="FETCH_MAX_BYTES")
    #: 3063: wall-clock budget of one download (the manifest, or one attempt
    #: at a page); the per-read timeouts restart with every byte.
    download_deadline_seconds: float = Field(300.0, alias="DOWNLOAD_DEADLINE_SECONDS")
    #: W14: the byte cap bounds the download, this one bounds what decoding it
    #: costs -- a few MB of JPEG can carry a gigapixel image, and htrflow
    #: decodes every page into memory. 0 turns the check off.
    max_image_pixels: int = Field(100_000_000, alias="MAX_IMAGE_PIXELS")
    #: Provenance the Job skeleton stamps: the pipeline's digest-pinned image
    #: and, from the image's own ENV, the htrflow it was built on. Both go
    #: into every ALTO (provenance.py) and the run manifest (publish.py).
    image_digest: str = Field("unknown", alias="IMAGE_DIGEST")
    htrflow_base_revision: str = Field("unknown", alias="HTRFLOW_BASE_REVISION")
    #: Audit 0923 W-4: which attempt this is. The Job controller writes the
    #: index's failure count on each pod (annotation ``batch.kubernetes.io/
    #: job-index-failure-count``, through the downward API) and the Job its
    #: ``backoffLimitPerIndex``; -1 is "not told", and then no attempt is
    #: taken for the last one.
    index_failure_count: int = Field(0, alias="INDEX_FAILURE_COUNT")
    backoff_limit_per_index: int = Field(-1, alias="BACKOFF_LIMIT_PER_INDEX")

    @field_validator("index_failure_count", "backoff_limit_per_index", mode="before")
    @classmethod
    def _unset_when_blank(cls, v: Any, info: ValidationInfo) -> Any:
        """A downward-API variable whose annotation is absent is empty, and
        must read as unset rather than fail the run as a bad setting."""
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[str(info.field_name)].default
        return v

    @property
    def last_attempt(self) -> bool:
        """No retry follows this pod if it fails."""
        limit = self.backoff_limit_per_index
        return limit >= 0 and self.index_failure_count >= limit

    @field_validator("volume_ref", "pipeline_id")
    @classmethod
    def _key_shaped(cls, v: str) -> str:
        """W12: both go verbatim into every S3 key this run writes
        (``<pipeline>/<volume>/…``) and into the public ``viewer_url``. A
        campaign file is where they come from, so the shape is checked once,
        here, and a name that would write outside the volume's own prefix --
        or point a reader's viewer somewhere else -- fails the run
        permanently instead. ``.`` and ``..`` are refused outright: an S3 key
        is an opaque string, but the URLs built from one are resolved by
        whatever reads them."""
        if not _KEY_SAFE.fullmatch(v) or ".." in v or v == ".":
            raise ValueError(
                "VOLUME_REF and PIPELINE_ID may contain only letters, digits, "
                f"'.', '_' and '-', may not be '.' and may not contain '..': {v!r}"
            )
        return v

    @field_validator("s3_prefix")
    @classmethod
    def _strip_slashes(cls, v: str) -> str:
        return v.strip("/")  # the only value the wrapper edits

    @classmethod
    def env_names(cls) -> list[str]:
        """The env var behind each field, in declaration order."""
        return [f.alias or name for name, f in cls.model_fields.items()]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        missing = [
            f.alias or name
            for name, f in cls.model_fields.items()
            if f.is_required() and not env.get(f.alias or name)
        ]
        if missing:
            raise ConfigError(f"missing required env: {', '.join(missing)}")
        if bool(env.get("IIIF_MANIFEST_URL")) == bool(env.get("IMAGES")):
            raise ConfigError("exactly one of IIIF_MANIFEST_URL or IMAGES must be set")
        kwargs: dict[str, Any] = {k: env[k] for k in cls.env_names() if k in env}
        return cls(**kwargs)

    def root_key(self, rel: str) -> str:
        """A bucket-root key under ``S3_PREFIX`` — the one place that join is
        written (``volume_prefix`` is its per-volume counterpart)."""
        return f"{self.s3_prefix}/{rel}" if self.s3_prefix else rel

    @property
    def image_urls(self) -> list[str]:
        """The URLs in ``IMAGES``, split on whitespace -- the one character a
        URL can never contain. A comma can: the IIIF Image API writes the size
        into the path (``/full/2500,/0/default.jpg``), so the old comma split
        tore that URL in half and the volume died in setup with ``IMAGES URL
        must be an http(s) URL: /0/default.jpg`` (2026-09-14).

        The converter joins them with a single space and refuses an ``images:``
        entry carrying whitespace of its own. Its ``models.split_image_urls``
        is this same function, including the comma branch below, which reads a
        line rendered before that fix; that package documents the rule and
        when to delete the branch, and its tests pin the two together, because
        this image must not carry the converter's Kubernetes client."""
        urls = self.images.split()
        if len(urls) == 1 and "," in urls[0]:
            parts = urls[0].split(",")
            if all(p.startswith(("http://", "https://")) for p in parts):
                return parts
        return urls

    @property
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
