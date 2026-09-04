"""FastAPI wiring: the read API plus the built site it is served with.

``/api/v1/…`` is GET-only over a ``kube.Reader`` (no auth — see the package
docstring / D8). ``reader`` is duck-typed — ``list_jobs``, ``get_job``,
``get_configmap``, ``list_pods`` and a ``cfg`` attribute — so tests wire a
fake and never touch a cluster.

Everything else on the port is the web front: the campaign browser SPA, the
Universal Viewer at ``/uv.html`` and the runtime ``/config.js``, mounted from
``STATIC_DIR`` AFTER the API routes so no file can shadow ``/api/v1/…``.
Until Task 17 this was a separate nginx image proxying ``/api/`` here; the
mount, the ``/log`` rewrite and the security headers below are that image's
whole job.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import projection

#: Exactly what the retired nginx config sent (chart 0.3.0's viewer template).
#: Script/style/connect sources are governed by the SvelteKit build's own
#: ``<meta http-equiv>`` CSP (kit.csp); a header must not be stricter than it,
#: since the browser enforces the intersection — so this one only forbids
#: framing, which a meta tag cannot express.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'",
}

#: Where the image puts the built site (.docker/htrflow-web.dockerfile).
DEFAULT_STATIC_DIR = "/app/static"

#: FastAPI, unlike Starlette's own Route, does not add HEAD to a GET route —
#: and an unhandled HEAD would fall through to the static mount below and
#: 404. Every route here is safe under HEAD (the body is simply dropped).
GET_HEAD = ["GET", "HEAD"]


def static_dir_from_env() -> Path:
    """The built site's directory: ``HTRFLOW_WEB_STATIC``, else the image's."""
    return Path(os.environ.get("HTRFLOW_WEB_STATIC") or DEFAULT_STATIC_DIR)


class BuiltSite(StaticFiles):
    """StaticFiles that also resolves ``/log`` to adapter-static's ``log.html``.

    SvelteKit's static adapter emits one ``<route>.html`` per prerendered
    page, so a direct visit or a refresh of ``/log`` has to be mapped by the
    server (nginx did it with ``try_files /log.html =404``). Extensionless
    paths only: a request for ``config.js`` must stay a 404 when it is
    missing rather than become ``config.js.html``.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # StaticFiles normpaths the request path, so "/" arrives as "."
            # (and "" if that ever changes): html=True has already tried
            # index.html for it, and ".html" would be a nonsense lookup.
            root = path in ("", ".", "/")
            if exc.status_code != 404 or root or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response(path + ".html", scope)


class NoCluster:
    """Reader stand-in for site-only mode: every API call is a clean 503.

    ``HTRFLOW_WEB_SITE_ONLY`` (the local compose stack) has the site but no
    apiserver. Wiring this instead of a ``kube.Reader`` keeps the routes
    registered — so ``/api/v1/…`` answers honestly rather than falling
    through to the static mount as a 404 or blowing up as a 500.
    """

    cfg = None

    def _no_cluster(self, *_args, **_kwargs):
        raise HTTPException(
            status_code=503,
            detail="site-only mode (HTRFLOW_WEB_SITE_ONLY): no cluster to read",
        )

    list_jobs = list_warmups = get_job = get_configmap = list_pods = _no_cluster


def create_app(reader, static_dir: Path | str | None = None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.api_route("/healthz", methods=GET_HEAD)
    def healthz() -> dict:
        return {"ok": True}

    @app.api_route("/api/v1/jobs", methods=GET_HEAD)
    def list_jobs() -> list[dict]:
        jobs = sorted(
            reader.list_jobs(),
            key=lambda j: (j.get("metadata") or {}).get("creationTimestamp", ""),
            reverse=True,
        )
        warmup_jobs = reader.list_warmups()
        reasons: dict[tuple[str, str], dict | None] = {}
        return [
            projection.summarize(
                job, reader.cfg, _warmup_status(job, warmup_jobs, reasons)
            )
            for job in jobs
        ]

    @app.api_route("/api/v1/jobs/{namespace}/{name}", methods=GET_HEAD)
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
        pipe_name = projection.configmap_ref(job, "pipeline")
        pipeline_cm = reader.get_configmap(namespace, pipe_name) if pipe_name else None
        pods = reader.list_pods(namespace, name)
        warmup = _warmup_status(job, reader.list_warmups(), {})
        return projection.detail(
            job, configmap, pods, reader.cfg, offset, limit, pipeline_cm, warmup=warmup
        )

    def _warmup_status(
        job: dict, warmup_jobs: list[dict], reasons: dict[tuple[str, str], dict | None]
    ) -> dict:
        warmup_job = projection.match_warmup(job, warmup_jobs)
        if warmup_job is None:
            return {"phase": "missing"}
        phase = projection.warmup_phase(warmup_job)
        if phase != "failed":
            return {"phase": phase}
        namespace = (job.get("metadata") or {}).get("namespace", "")
        name = (warmup_job.get("metadata") or {}).get("name", "")
        # Several campaigns can share one failed warm-up: list its pods once
        # per request. Keyed with the namespace -- warm-up names carry none.
        if (namespace, name) not in reasons:
            pods = reader.list_pods(namespace, name)
            reasons[namespace, name] = (
                projection.wrapper_reason(projection.newest(pods), "warmup")
                if pods
                else None
            )
        reason = reasons[namespace, name]
        return {"phase": phase, "reason": reason} if reason else {"phase": phase}

    # Last, so the routes above win over any file of the same name. Absent
    # outside the image (a local `uv run htrflow-web` builds no site), which
    # is not an error: the API is then all there is.
    static = Path(static_dir) if static_dir is not None else static_dir_from_env()
    if static.is_dir():
        app.mount("/", BuiltSite(directory=static, html=True), name="site")

    return app
