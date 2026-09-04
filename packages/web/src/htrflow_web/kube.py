"""kubernetes.client adapter for the read API.

Read-only: every method here is a get/list against Jobs, ConfigMaps or Pods.
Nothing in this module creates, updates or removes a cluster object — the
RBAC granted to the service is get/list/watch only (charts/htrflow-batch
templates/api.yaml), and a test greps this package's source to keep it that
way.

``Reader`` returns the plain dicts the Kubernetes API server itself sends
back (camelCase field names), which is exactly the shape ``projection.py``'s
pure functions expect.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from kubernetes import client, config

#: Selects campaign progress Jobs only — excludes the per-pipeline warm-up
#: Jobs, which carry ``managed-by=converter`` too but not ``app`` or
#: ``campaign`` (packages/converter/src/htrflow_converter/render.py).
LABEL_SELECTOR = "app=htrflow-batch,htrflow.riksarkivet.se/managed-by=converter"

_WARMUP_SELECTOR = "app=htrflow-warmup,htrflow.riksarkivet.se/managed-by=converter"

_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_DEFAULT_NAMESPACE = "htr-batch"


def _own_namespace() -> str:
    try:
        with open(_NAMESPACE_FILE) as f:
            return f.read().strip() or _DEFAULT_NAMESPACE
    except OSError:
        return _DEFAULT_NAMESPACE


@dataclass(frozen=True)
class Config:
    public_results_base: str
    namespaces: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = env.get("HTRFLOW_PUBLIC_RESULTS_BASE")
        if not base:
            raise RuntimeError("HTRFLOW_PUBLIC_RESULTS_BASE is required")
        raw = (env.get("HTRFLOW_NAMESPACES") or "").strip()
        namespaces = tuple(n.strip() for n in raw.split(",") if n.strip())
        return cls(
            public_results_base=base.rstrip("/"),
            namespaces=namespaces or (_own_namespace(),),
        )


def _read(api: object, method: str, *args: object, **kwargs: object) -> dict | None:
    """Call a get/list method with ``_preload_content=False`` and decode the
    raw server JSON, so callers get the same camelCase dicts the API server
    sends — no typed-model round trip. 404 -> ``None``."""
    fn = getattr(api, method)
    try:
        resp = fn(*args, _preload_content=False, **kwargs)
    except client.ApiException as e:
        if e.status == 404:
            return None
        raise
    return json.loads(resp.data)


class Reader:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def _list_jobs(self, selector: str) -> list[dict]:
        jobs: list[dict] = []
        for ns in self.cfg.namespaces:
            body = _read(self.batch, "list_namespaced_job", ns, label_selector=selector)
            jobs.extend((body or {}).get("items", []))
        return jobs

    def list_jobs(self) -> list[dict]:
        return self._list_jobs(LABEL_SELECTOR)

    def list_warmups(self) -> list[dict]:
        return self._list_jobs(_WARMUP_SELECTOR)

    def get_job(self, namespace: str, name: str) -> dict | None:
        return _read(self.batch, "read_namespaced_job", name, namespace)

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return _read(self.core, "read_namespaced_config_map", name, namespace)

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        body = _read(
            self.core,
            "list_namespaced_pod",
            namespace,
            label_selector=f"batch.kubernetes.io/job-name={job_name}",
        )
        return (body or {}).get("items", [])
