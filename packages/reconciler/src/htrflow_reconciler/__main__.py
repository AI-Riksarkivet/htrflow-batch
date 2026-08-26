"""CronJob entrypoint: one tick with real adapters, config from env."""

import json
import logging
import tempfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import httpx
from pydantic_settings import BaseSettings

from .gitrepo import DEFAULT_TIMEOUT, checkout
from .jobspec import ReconcilerConfig
from .k8s import Cluster
from .main import tick
from .s3 import Bucket


class Settings(BaseSettings):
    campaigns_repo_url: str
    campaigns_repo_web_url: str = ""
    public_results_base: str
    s3_endpoint: str = ""
    s3_bucket: str = "htr-results"
    campaigns_dir: Path = Path(tempfile.gettempdir()) / "campaigns"
    reconciler_window: int = 20
    reconciler_attempt_cap: int = 3
    # Cluster coupling. The defaults reproduce the PoC, but the chart supplies
    # all three so a release can be renamed or re-namespaced without a rebuild
    # (RECONCILER_NAMESPACE comes from the downward API).
    reconciler_namespace: str = "htr-batch"
    reconciler_queue: str = "htr-batch"
    reconciler_s3_secret: str = "htr-batch-s3"
    reconciler_data_pvc: str = "htr-test-data"
    # Audit remediation plan, "Reconciler env" contract: names and defaults.
    reconciler_tick_seconds: int = 300
    reconciler_lease_name: str = "htr-reconciler"
    reconciler_allowed_image_repos: str = ""  # comma-separated; empty = any
    reconciler_require_model_revision: bool = False
    reconciler_job_min_deadline_seconds: int = 21600
    reconciler_job_seconds_per_page: int = 30
    reconciler_job_runtime_class: str = "nvidia"
    reconciler_job_node_selector: dict[str, str] = {}
    reconciler_job_tolerations: list[dict] = []
    reconciler_max_validations_per_tick: int = 50
    reconciler_fetch_max_bytes: int = 16 * 1024 * 1024
    # One tick's wall-clock budget (the CronJob's activeDeadlineSeconds); the
    # git timeout is clamped to it so a hung clone cannot outlive the pod that
    # would have reported it (audit O7).
    reconciler_tick_deadline_seconds: int = 600
    reconciler_git_timeout: int = DEFAULT_TIMEOUT


def _git_timeout(settings: Settings) -> int:
    return max(
        1,
        min(settings.reconciler_git_timeout, settings.reconciler_tick_deadline_seconds),
    )


def _internal_results_base(settings: Settings) -> str:
    """Jobs fetch synthetic manifests via the in-cluster S3 endpoint; with
    no explicit endpoint (real AWS) the public base works everywhere."""
    if not settings.s3_endpoint:
        return ""
    return f"{settings.s3_endpoint.rstrip('/')}/{settings.s3_bucket}"


#: Per-manifest fetch budget. Validation runs in a bounded, pooled batch per
#: tick, so a slow host costs one of these, not the whole tick (audit X1).
FETCH_TIMEOUT_SECONDS = 10.0


def fetch_json(
    url: str, *, max_bytes: int, client: httpx.Client | None = None
) -> dict | None:
    """GET a manifest with the S5 guards: http(s) only, ``max_redirects=3``,
    body capped at ``max_bytes`` (by header, then while streaming). Anything
    else — network error, non-200, junk — is ``None``: unreachable, retried
    after the back-off, never an exception out of the tick."""
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    try:
        with client or httpx.Client(
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, max_redirects=3
        ) as c:
            with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                declared = r.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    return None
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        return None
        doc = json.loads(bytes(buf))
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001 — any failure is "unreachable"
        return None


def build_config(settings: Settings) -> ReconcilerConfig:
    repos = tuple(
        r.strip()
        for r in settings.reconciler_allowed_image_repos.split(",")
        if r.strip()
    )
    return ReconcilerConfig(
        public_results_base=settings.public_results_base,
        internal_results_base=_internal_results_base(settings),
        campaigns_repo_url=settings.campaigns_repo_url,
        campaigns_repo_web_url=settings.campaigns_repo_web_url,
        namespace=settings.reconciler_namespace,
        queue=settings.reconciler_queue,
        s3_secret=settings.reconciler_s3_secret,
        data_pvc=settings.reconciler_data_pvc,
        window=settings.reconciler_window,
        attempt_cap=settings.reconciler_attempt_cap,
        tick_seconds=settings.reconciler_tick_seconds,
        tick_deadline_seconds=settings.reconciler_tick_deadline_seconds,
        lease_name=settings.reconciler_lease_name,
        allowed_image_repos=repos,
        require_model_revision=settings.reconciler_require_model_revision,
        job_min_deadline_seconds=settings.reconciler_job_min_deadline_seconds,
        job_seconds_per_page=settings.reconciler_job_seconds_per_page,
        job_runtime_class=settings.reconciler_job_runtime_class,
        job_node_selector=settings.reconciler_job_node_selector,
        job_tolerations=settings.reconciler_job_tolerations,
        max_validations_per_tick=settings.reconciler_max_validations_per_tick,
    )


def run() -> None:
    settings = Settings()  # reads CAMPAIGNS_REPO_URL etc.; raises if missing
    cfg = build_config(settings)
    repo = checkout(
        settings.campaigns_repo_url,
        settings.campaigns_dir,
        timeout=_git_timeout(settings),
    )
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint or None)
    bucket = Bucket(client, settings.s3_bucket)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fetch = partial(fetch_json, max_bytes=settings.reconciler_fetch_max_bytes)
    tick(repo, bucket, Cluster(cfg.namespace), cfg, now, fetch_json=fetch)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    run()
