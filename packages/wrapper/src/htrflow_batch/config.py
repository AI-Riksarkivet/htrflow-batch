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


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    volume_ref: str
    pipeline_path: str
    pipeline_id: str
    s3_endpoint: str
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
    max_seconds: int = 0  # 0 = no per-volume wall-clock budget (docs: wrapper)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        kwargs: dict[str, Any] = {}
        missing = [k for _, k in _REQUIRED if not env.get(k)]
        if missing:
            raise ConfigError(f"missing required env: {', '.join(missing)}")
        for attr, key in _REQUIRED:
            kwargs[attr] = env[key]
        kwargs["manifest_url"] = env.get("IIIF_MANIFEST_URL", "")
        kwargs["images"] = env.get("IMAGES", "")
        if bool(kwargs["manifest_url"]) == bool(kwargs["images"]):
            raise ConfigError("exactly one of IIIF_MANIFEST_URL or IMAGES must be set")
        kwargs["s3_endpoint"] = env.get("S3_ENDPOINT", "")
        kwargs["s3_prefix"] = env.get("S3_PREFIX", "").strip("/")
        kwargs["max_image_width"] = int(env.get("MAX_IMAGE_WIDTH", "2500"))
        kwargs["resume"] = _bool(env.get("RESUME", "true"))
        kwargs["lookahead_pages"] = int(env.get("LOOKAHEAD_PAGES", "64"))
        kwargs["max_pages"] = int(env.get("MAX_PAGES", "0"))
        kwargs["workdir"] = env.get("WORKDIR_PATH", "/work")
        kwargs["download_concurrency"] = int(env.get("DOWNLOAD_CONCURRENCY", "12"))
        kwargs["log_ship_seconds"] = float(env.get("LOG_SHIP_SECONDS", "15"))
        kwargs["manifest_max_bytes"] = int(
            env.get("MANIFEST_MAX_BYTES", str(16 * 1024 * 1024))
        )
        kwargs["fetch_max_bytes"] = int(
            env.get("FETCH_MAX_BYTES", str(64 * 1024 * 1024))
        )
        kwargs["max_seconds"] = int(env.get("MAX_SECONDS", "0"))
        return cls(**kwargs)

    @property
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
