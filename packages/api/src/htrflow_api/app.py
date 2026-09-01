"""FastAPI wiring: GET-only ``/api/v1/jobs`` over a ``kube.Reader``.

No auth (see the package docstring / D8). ``reader`` is duck-typed —
``list_jobs``, ``get_job``, ``get_configmap``, ``list_pods`` and a ``cfg``
attribute — so tests wire a fake and never touch a cluster.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from . import projection


def create_app(reader) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/v1/jobs")
    def list_jobs() -> list[dict]:
        jobs = sorted(
            reader.list_jobs(),
            key=lambda j: (j.get("metadata") or {}).get("creationTimestamp", ""),
            reverse=True,
        )
        return [projection.summarize(job, reader.cfg) for job in jobs]

    @app.get("/api/v1/jobs/{namespace}/{name}")
    def get_job(
        namespace: str,
        name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict:
        job = reader.get_job(namespace, name)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        cm_name = projection.configmap_ref(job)
        configmap = reader.get_configmap(namespace, cm_name) if cm_name else None
        pods = reader.list_pods(namespace, name)
        return projection.detail(job, configmap, pods, reader.cfg, offset, limit)

    return app
