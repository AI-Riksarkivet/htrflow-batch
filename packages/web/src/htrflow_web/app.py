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

import logging
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import projection
from .progress import ProgressReader

_LOG = logging.getLogger(__name__)

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

#: This package's own version, read off the installed distribution. Reported
#: beside the deployed tag, never instead of it: the workspace members are
#: versioned separately, and what an operator deployed is the image's tag.
#: A source tree with nothing installed still has to answer something.
PACKAGE = "htrflow-web"
try:
    WEB_VERSION = metadata.version(PACKAGE)
except metadata.PackageNotFoundError:  # pragma: no cover - installed in CI
    WEB_VERSION = "unknown"

#: What ``HTRFLOW_BATCH_VERSION`` says outside an image (kube.Config's default
#: and the dockerfiles'): a build nobody tagged.
DEV_VERSION = "dev"

#: Where the image puts the built site (.docker/htrflow-web.dockerfile).
DEFAULT_STATIC_DIR = "/app/static"

#: FastAPI, unlike Starlette's own Route, does not add HEAD to a GET route —
#: and an unhandled HEAD would fall through to the static mount below and
#: 404. Every route here is safe under HEAD (the body is simply dropped).
GET_HEAD = ["GET", "HEAD"]


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
    list_configmaps = _no_cluster
    # Deliberately no `apply_configmap`: site-only mode has no cluster to
    # write the campaign record to, and `_record` below asks for the
    # attribute rather than calling into a 503 on every request (B76).


def create_app(
    reader,
    static_dir: Path | str | None = None,
    batch_version: str = DEV_VERSION,
    progress=None,
) -> FastAPI:
    """``batch_version`` is the deployed image's tag, passed in by
    ``__main__`` from ``kube.Config`` -- this module reads no environment of
    its own, and site-only mode has no ``cfg`` on its reader to take it from.

    ``progress`` is the reader of the volumes' progress files in the
    results bucket — one per app, so its HTTP client and its few-second cache
    are shared by every request. Injectable so tests need no bucket.

    Not built at all in site-only mode (``reader.cfg is None``, ``NoCluster``
    below): every route that would use it 503s before reaching
    ``progress.fetch`` (``reader.get_job`` raises first), so the HTTP client
    it would open has nothing to ever ask."""
    app = FastAPI()
    site_only = reader.cfg is None
    if progress is None and not site_only:
        progress = ProgressReader()

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.api_route("/healthz", methods=GET_HEAD)
    def healthz() -> dict:
        return {"ok": True}

    @app.api_route("/api/v1/version", methods=GET_HEAD)
    def version() -> dict:
        return {"version": batch_version, "web": WEB_VERSION}

    def _record(row: dict, live: dict | None, failures: list[dict] | None) -> None:
        """Write the campaign's status ConfigMap when this request saw
        something the stored one does not already say (B76).

        Merged over what is there and never shrinking it
        (``projection.merge_record``): each endpoint observes a different
        part (only the detail one reads pods, so only it knows the failed
        volumes, and even it stops seeing them once the pods are collected),
        and an unchanged body is not sent at all -- an idle status page
        polls, and every poll would otherwise be a write. Never fatal: a
        record the API could not write is a record a few minutes old, while
        a 500 is a status page nobody can read."""
        if not hasattr(reader, "apply_configmap"):
            return  # site-only: no cluster
        stored = (live or {}).get("data") or {}
        data = projection.merge_record(stored, projection.status_record(row, failures))
        if data == stored:
            return
        cm = projection.status_configmap(row, data)
        try:
            reader.apply_configmap(cm)
        except Exception as e:  # noqa: BLE001 - any client error, same answer
            _LOG.warning("could not write %s: %s", cm["metadata"]["name"], e)

    def _campaign_configmaps() -> tuple[dict, dict]:
        """A campaign's two ConfigMaps, each by (namespace, campaign name):
        the converter's record (``volumes.txt``, provenance) and the status
        one this service writes beside it. One list call for the whole page
        rather than a get per campaign."""
        records: dict[tuple[str, str], dict] = {}
        statuses: dict[tuple[str, str], dict] = {}
        if not hasattr(reader, "list_configmaps"):
            return records, statuses
        for cm in reader.list_configmaps():
            meta = cm.get("metadata") or {}
            name, ns = meta.get("name", ""), meta.get("namespace", "")
            if not name.startswith("campaign-"):
                continue
            stem = name.removeprefix("campaign-")
            if stem.endswith(projection.STATUS_SUFFIX):
                statuses[ns, stem.removesuffix(projection.STATUS_SUFFIX)] = cm
            else:
                records[ns, stem] = cm
        return records, statuses

    @app.api_route("/api/v1/jobs", methods=GET_HEAD)
    def list_jobs() -> list[dict]:
        jobs = sorted(
            reader.list_jobs(),
            key=lambda j: (j.get("metadata") or {}).get("creationTimestamp", ""),
            reverse=True,
        )
        warmup_jobs = reader.list_warmups()
        reasons: dict[tuple[str, str], dict | None] = {}
        rows = [
            projection.summarize(
                job, reader.cfg, _warmup_status(job, warmup_jobs, reasons)
            )
            for job in jobs
        ]
        records, statuses = _campaign_configmaps()
        for row in rows:
            _record(row, statuses.get((row["namespace"], row["name"])), None)
        # A campaign whose Job the TTL reaped is still a campaign: its two
        # ConfigMaps have no TTL, and this list is where an operator looks
        # for it (B76). Additive -- a live Job always wins over its record.
        live = {(row["namespace"], row["name"]) for row in rows}
        for key, record in records.items():
            status = statuses.get(key)
            if key in live or status is None:
                continue
            gone = projection.record_summary(
                record,
                status,
                reader.cfg,
                _warmup_status(record, warmup_jobs, reasons),
            )
            if gone is not None:
                rows.append(gone)
        rows.sort(key=lambda row: row["createdAt"] or "", reverse=True)
        return rows

    @app.api_route("/api/v1/jobs/{namespace}/{name}", methods=GET_HEAD)
    def get_job(
        namespace: str,
        name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict:
        job = reader.get_job(namespace, name)
        if job is None:
            return _reaped_detail(namespace, name)
        cm_name = projection.configmap_ref(job)
        configmap = reader.get_configmap(namespace, cm_name) if cm_name else None
        pipe_name = projection.configmap_ref(job, "pipeline")
        pipeline_cm = reader.get_configmap(namespace, pipe_name) if pipe_name else None
        pods = reader.list_pods(namespace, name)
        warmup = _warmup_status(job, reader.list_warmups(), {})
        body = projection.detail(
            job,
            configmap,
            pods,
            reader.cfg,
            offset,
            limit,
            pipeline_cm,
            warmup=warmup,
            fetch_progress=progress.fetch if progress is not None else None,
        )
        status_name = f"{cm_name or 'campaign-' + name}{projection.STATUS_SUFFIX}"
        _record(body, reader.get_configmap(namespace, status_name), body["failures"])
        return body

    def _reaped_detail(namespace: str, name: str) -> dict:
        """The campaign page of a campaign whose Job is gone. The pipeline
        ConfigMap is asked for by name here -- the one place this package
        rebuilds the converter's ``htr-pipeline-<id>`` convention instead of
        reading it off a pod spec (``projection.configmap_ref``), because
        there is no pod spec left to read it off (B76)."""
        record = reader.get_configmap(namespace, f"campaign-{name}")
        suffix = projection.STATUS_SUFFIX
        status = reader.get_configmap(namespace, f"campaign-{name}{suffix}")
        row = (
            projection.record_summary(
                record,
                status,
                reader.cfg,
                _warmup_status(record, reader.list_warmups(), {}),
            )
            if record and status
            else None
        )
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        pipe = reader.get_configmap(namespace, f"htr-pipeline-{row['pipeline']}")
        return projection.record_detail(row, status, reader.cfg, pipe)

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
    static = Path(static_dir or DEFAULT_STATIC_DIR)
    if static.is_dir():
        app.mount("/", BuiltSite(directory=static, html=True), name="site")

    return app
