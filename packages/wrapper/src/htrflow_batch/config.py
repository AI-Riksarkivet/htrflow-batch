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

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigError(ValueError):
    pass


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
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
