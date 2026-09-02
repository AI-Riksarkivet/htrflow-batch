"""Env contract for the wrapper (docs: wrapper)."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict


class ConfigError(ValueError):
    pass


_REQUIRED = [
    ("volume_ref", "VOLUME_REF"),
    ("pipeline_path", "PIPELINE_PATH"),
    ("pipeline_id", "PIPELINE_ID"),
    ("s3_bucket", "S3_BUCKET"),
    ("public_results_base", "PUBLIC_RESULTS_BASE"),
]


#: Optional fields: (attribute, env var). The raw string goes to pydantic,
#: which coerces it ("64" -> int, "15" -> float, "off"/"true" -> bool) and
#: raises ValidationError -- a ValueError, so _main classifies it PERMANENT
#: (exit 13) -- for one it cannot. The class-level default below is therefore
#: the single source of truth for every default.
_OPTIONAL = [
    ("s3_endpoint", "S3_ENDPOINT"),
    ("max_image_width", "MAX_IMAGE_WIDTH"),
    ("resume", "RESUME"),
    ("lookahead_pages", "LOOKAHEAD_PAGES"),
    ("max_pages", "MAX_PAGES"),
    ("workdir", "WORKDIR_PATH"),
    ("download_concurrency", "DOWNLOAD_CONCURRENCY"),
    ("log_ship_seconds", "LOG_SHIP_SECONDS"),
    ("manifest_max_bytes", "MANIFEST_MAX_BYTES"),
    ("fetch_max_bytes", "FETCH_MAX_BYTES"),
]


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    volume_ref: str
    pipeline_path: str
    pipeline_id: str
    s3_endpoint: str = ""
    s3_bucket: str
    public_results_base: str
    # Exactly one of these is set (docs: wrapper, IMAGES) — see from_env.
    manifest_url: str = ""
    images: str = ""
    s3_prefix: str = ""
    max_image_width: int = 2500
    resume: bool = True
    lookahead_pages: int = 64
    max_pages: int = 0
    workdir: str = "/work"
    download_concurrency: int = 12
    log_ship_seconds: float = 15.0  # 0 disables live shipping of the run log
    # S5 byte caps on fetches driven by campaign data (docs: wrapper)
    manifest_max_bytes: int = 16 * 1024 * 1024
    fetch_max_bytes: int = 64 * 1024 * 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        missing = [k for _, k in _REQUIRED if not env.get(k)]
        if missing:
            raise ConfigError(f"missing required env: {', '.join(missing)}")
        kwargs: dict[str, Any] = {attr: env[key] for attr, key in _REQUIRED}
        kwargs["manifest_url"] = env.get("IIIF_MANIFEST_URL", "")
        kwargs["images"] = env.get("IMAGES", "")
        if bool(kwargs["manifest_url"]) == bool(kwargs["images"]):
            raise ConfigError("exactly one of IIIF_MANIFEST_URL or IMAGES must be set")
        for attr, key in _OPTIONAL:
            if key in env:
                kwargs[attr] = env[key]
        if "S3_PREFIX" in env:
            # the only value the wrapper edits before pydantic sees it
            kwargs["s3_prefix"] = env["S3_PREFIX"].strip("/")
        return cls(**kwargs)

    def root_key(self, rel: str) -> str:
        """A bucket-root key under ``S3_PREFIX`` — the one place that join is
        written (``volume_prefix`` is its per-volume counterpart)."""
        return f"{self.s3_prefix}/{rel}" if self.s3_prefix else rel

    @property
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
