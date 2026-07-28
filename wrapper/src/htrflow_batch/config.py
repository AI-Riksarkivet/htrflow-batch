"""Env contract for the wrapper (DESIGN.md §5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class ConfigError(ValueError):
    pass


_REQUIRED = [
    ("volume_ref", "VOLUME_REF"),
    ("manifest_url", "IIIF_MANIFEST_URL"),
    ("pipeline_path", "PIPELINE_PATH"),
    ("pipeline_id", "PIPELINE_ID"),
    ("s3_bucket", "S3_BUCKET"),
    ("public_results_base", "PUBLIC_RESULTS_BASE"),
]


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    volume_ref: str
    manifest_url: str
    pipeline_path: str
    pipeline_id: str
    s3_endpoint: str
    s3_bucket: str
    public_results_base: str
    s3_prefix: str = ""
    max_image_width: int = 2500
    resume: bool = True
    lookahead_pages: int = 64
    max_pages: int = 0
    workdir: str = "/work"
    download_concurrency: int = 12

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        kwargs = {}
        missing = [k for _, k in _REQUIRED if not env.get(k)]
        if missing:
            raise ConfigError(f"missing required env: {', '.join(missing)}")
        for attr, key in _REQUIRED:
            kwargs[attr] = env[key]
        kwargs["s3_endpoint"] = env.get("S3_ENDPOINT", "")
        kwargs["s3_prefix"] = env.get("S3_PREFIX", "").strip("/")
        kwargs["max_image_width"] = int(env.get("MAX_IMAGE_WIDTH", "2500"))
        kwargs["resume"] = _bool(env.get("RESUME", "true"))
        kwargs["lookahead_pages"] = int(env.get("LOOKAHEAD_PAGES", "64"))
        kwargs["max_pages"] = int(env.get("MAX_PAGES", "0"))
        kwargs["workdir"] = env.get("WORKDIR_PATH", "/work")
        kwargs["download_concurrency"] = int(env.get("DOWNLOAD_CONCURRENCY", "12"))
        return cls(**kwargs)

    @property
    def volume_prefix(self) -> str:
        parts = [p for p in (self.s3_prefix, self.pipeline_id, self.volume_ref) if p]
        return "/".join(parts)
