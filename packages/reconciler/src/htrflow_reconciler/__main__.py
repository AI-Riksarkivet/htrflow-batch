"""CronJob entrypoint: one tick with real adapters, config from env."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
import httpx
from pydantic_settings import BaseSettings

from .gitrepo import checkout
from .jobspec import ReconcilerConfig
from .k8s import Cluster
from .main import tick
from .s3 import Bucket


class Settings(BaseSettings):
    campaigns_repo_url: str
    public_results_base: str
    s3_endpoint: str = ""
    s3_bucket: str = "htr-results"
    campaigns_dir: Path = Path(tempfile.gettempdir()) / "campaigns"
    reconciler_window: int = 20
    reconciler_attempt_cap: int = 3


def _fetch_json(url: str) -> dict | None:
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def run() -> None:
    settings = Settings()  # reads CAMPAIGNS_REPO_URL etc.; raises if missing
    cfg = ReconcilerConfig(
        public_results_base=settings.public_results_base,
        window=settings.reconciler_window,
        attempt_cap=settings.reconciler_attempt_cap,
    )
    repo = checkout(settings.campaigns_repo_url, settings.campaigns_dir)
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint or None)
    bucket = Bucket(client, settings.s3_bucket)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tick(repo, bucket, Cluster(cfg.namespace), cfg, now, fetch_json=_fetch_json)


if __name__ == "__main__":
    run()
