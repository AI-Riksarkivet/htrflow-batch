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

import base64
import hashlib
import json
import logging
import os
import re
import time
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import projection
from .kube import ApplyConflict, ClusterUnavailable, is_campaign
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

#: The Universal Viewer is a third-party page with no <meta> CSP of its own,
#: and until the 2026-09-14 audit the only thing forbidden on it was framing.
#: What the built viewer actually needs, read off /app/static in the image:
#: its bundle from a file beside it ('self'); ONE inline <script> and ONE
#: inline <style>, which is what the hashes below are for; images from
#: anywhere, including the data: URIs in uv.css and the blob: tiles
#: OpenSeadragon builds; a manifest fetched from wherever the URL fragment
#: points. Not needed and so not granted: 'unsafe-eval' (the one
#: `new Function` in the bundle is webpack's globalThis probe, inside a
#: try/catch with a `window` fallback) and any third-party script origin.
#: `worker-src blob:` is granted for the 3D and audio decoders in UV's lazy
#: chunks, which an image manifest never loads. Styles are 'unsafe-inline'
#: and nothing else: UV lays itself out through `style=` attributes it
#: writes at runtime, which a hash cannot name, and a hash beside the
#: keyword would make a browser ignore the keyword (2026-09-16, the viewer
#: rendered unstyled under the hashed policy). Script stays hashed: an
#: injected style is a layout nuisance, an injected script is the bucket.
UV_CSP = (
    "default-src 'self'; script-src 'self'{scripts}; "
    "style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; img-src * data: blob:; connect-src *; "
    "worker-src 'self' blob:; frame-ancestors 'none'"
)


class _InlineScripts(HTMLParser):
    """The body of every <script> that has one of its own, exactly as a
    browser reads it -- one that loads a file has a `src` and is covered by
    'self' instead. Parsed, not matched: a pattern looking for `</script>`
    missed `</script >` and `</SCRIPT foo>`, which a browser closes a script
    on, and hashed the wrong text (code scanning 117). The stdlib parser
    reads a script's content as raw text up to its end tag, as the HTML
    spec does."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.bodies: list[str] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script" and all(name != "src" for name, _ in attrs):
            self._open = []

    def handle_data(self, data) -> None:
        if self._open is not None:
            self._open.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self._open is not None:
            self.bodies.append("".join(self._open))
            self._open = None


def _inline_scripts(html: str) -> list[str]:
    parser = _InlineScripts()
    parser.feed(html)
    parser.close()
    return parser.bodies


UV_PATH = "/uv.html"


def uv_csp(static: Path) -> str | None:
    """``UV_CSP`` with the built viewer's own inline blocks hashed into it,
    or ``None`` when there is no viewer to serve (a source checkout builds
    no site). Computed once per process: the file cannot change under a
    running container."""
    try:
        html = (static / UV_PATH.lstrip("/")).read_text(encoding="utf-8")
    except OSError:
        return None
    scripts = ""
    for body in _inline_scripts(html):
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        scripts += f" 'sha256-{digest}'"
    return UV_CSP.format(scripts=scripts)


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

#: How many campaign records one request may write. Each is a server-side
#: apply on the request's critical path, so a namespace of hundreds would
#: otherwise turn one page load into hundreds of sequential round trips.
#: What is left over is written by the next poll, and by the apply itself
#: (packages/converter ``render.status_configmap``), which is what actually
#: guarantees a terminal record exists.
RECORD_WRITES_PER_REQUEST = 20

#: How long a namespace whose write was refused is left alone. Long enough
#: that a denied grant costs one round trip per ten minutes rather than
#: twenty per page load; short enough that renewing the grant is visible
#: without restarting the service.
REFUSAL_COOLDOWN = 600.0

#: The one sentence a 502 says. The reader can do nothing about an RBAC
#: change or a busy API server except wait for the next poll, which the page
#: makes on its own.
CLUSTER_UNAVAILABLE_DETAIL = (
    "the Kubernetes API did not answer this request - the page retries on its own"
)

#: One DNS-1123 label -- what a Job, a ConfigMap and a namespace can each be
#: called. The length cap (63) is checked beside it.
DNS_1123 = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")

#: What /config.js says. `frontend/static/config.js` is the same file for a
#: `bun run dev` that has no service to ask.
CONFIG_JS = (
    "// Served by the read API, from its own environment.\n"
    'window.API_BASE = "/api/v1";\n'
    "window.RESULTS_BASE = {results_base};\n"
)

#: Where the image puts the built site (.docker/htrflow-web.dockerfile).
DEFAULT_STATIC_DIR = "/app/static"

#: FastAPI, unlike Starlette's own Route, does not add HEAD to a GET route —
#: and an unhandled HEAD would fall through to the static mount below and
#: 404. Every route here is safe under HEAD (the body is simply dropped).
GET_HEAD = ["GET", "HEAD"]


class BuiltSite(StaticFiles):
    """StaticFiles that also resolves ``/log`` to adapter-static's ``log.html``,
    and puts the viewer's policy on whatever response carries ``uv.html``.

    SvelteKit's static adapter emits one ``<route>.html`` per prerendered
    page, so a direct visit or a refresh of ``/log`` has to be mapped by the
    server (nginx did it with ``try_files /log.html =404``). Extensionless
    paths only: a request for ``config.js`` must stay a 404 when it is
    missing rather than become ``config.js.html``.

    The viewer's CSP is chosen by the file served, not by the request path:
    the retry above answers ``/uv`` with it and the normalised lookup answers
    ``/uv.html/`` and ``//uv.html``, and a path match left all of those with
    ``frame-ancestors 'none'`` alone (2026-09-17 audit, 3059). The middleware
    in ``create_app`` keeps a CSP a response already has.
    """

    def __init__(self, *args, viewer_csp: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.viewer_csp = viewer_csp
        d = self.directory  # None only for a packages-served StaticFiles: no viewer
        self.viewer = d and os.path.realpath(Path(d) / UV_PATH.lstrip("/"))

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        # lookup_path hands over a realpath, so an alias or a symlink to the
        # viewer compares equal here. A 304 is still the viewer's response.
        if self.viewer_csp and os.path.realpath(full_path) == self.viewer:
            response.headers["Content-Security-Policy"] = self.viewer_csp
        return response

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
    list_configmaps = apply_configmap = _no_cluster


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

    viewer_csp = uv_csp(Path(static_dir or DEFAULT_STATIC_DIR))

    @app.middleware("http")
    async def security_headers(request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            # Anything no handler claimed is otherwise answered by Starlette
            # OUTSIDE this middleware, as a plain-text 500 with none of the
            # headers below (2026-09-23 audit). Logged in full, never quoted.
            _LOG.exception("unhandled error on %s", request.url.path)
            response = JSONResponse(
                status_code=500, content={"detail": "internal error"}
            )
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(ClusterUnavailable)
    async def cluster_unavailable(request, exc) -> JSONResponse:
        """A read the API server refused or never answered. Registered as a
        handler rather than left to escape, because an escaping exception is
        answered by Starlette OUTSIDE the middleware above -- a plain-text
        500 with no nosniff and no frame-ancestors on it (2026-09-14 audit).
        The client's own message names verbs and identities and is logged,
        never sent."""
        _LOG.warning("cluster read failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": CLUSTER_UNAVAILABLE_DETAIL},
        )

    @app.api_route("/config.js", methods=GET_HEAD)
    def config_js() -> Response:
        """The page's runtime configuration, built from this process's own
        environment rather than read off a file in the image (2026-09-14
        audit). `RESULTS_BASE` is what the run-log route checks its `?log=`
        and `?manifest=` against, and a copy of it that an operator had to
        keep in step with HTRFLOW_PUBLIC_RESULTS_BASE would be wrong exactly
        when it mattered. Site-only mode has no cfg and says so with an
        empty base. The API is always same-origin: this file is served by
        the service that answers /api/v1."""
        base = getattr(reader.cfg, "public_results_base", "") or ""
        return Response(
            CONFIG_JS.format(results_base=json.dumps(base)),
            media_type="text/javascript",
        )

    @app.api_route("/healthz", methods=GET_HEAD)
    async def healthz() -> dict:
        """Answered on the event loop, never the thread pool every sync
        route shares: with requests stuck on a hung API server holding every
        worker, a pooled probe queued behind them, readiness failed, and the
        only replica left the Service (2026-09-23 audit). Liveness of the
        process is the question; the cluster's is each route's own 502."""
        return {"ok": True}

    @app.api_route("/api/v1/version", methods=GET_HEAD)
    def version() -> dict:
        return {"version": batch_version, "web": WEB_VERSION}

    # Namespaces whose last write was refused, and when. A denied RBAC grant
    # fails for every campaign on every poll -- a log nobody can read, and
    # RECORD_WRITES_PER_REQUEST server-side applies on the critical path of
    # every page load, for ever (2026-09-14 audit). Said once, then left
    # alone until the cooldown expires, so a grant that was renewed starts
    # working again on its own. (Per app, so a restart tries immediately.)
    refused: dict[str, float] = {}

    def _record(
        row: dict, job: dict, live: dict | None, failures: list[dict] | None
    ) -> bool:
        """Write the campaign's status ConfigMap when this request saw
        something the stored one does not already say (B76).

        Merged over what is there and never shrinking it
        (``projection.merge_record``): each endpoint observes a different
        part (only the detail one reads pods, so only it knows the failed
        volumes, and even it stops seeing them once the pods are collected),
        and an unchanged body is not sent at all -- an idle status page
        polls, and every poll would otherwise be a write. Which fields are
        this API's to send and which are `apply`'s is
        ``projection.record_write``'s. Never fatal: a record the API could
        not write is a record a few minutes old, while a 500 is a status
        page nobody can read."""
        refused_at = refused.get(row["namespace"])
        if refused_at is not None and time.monotonic() - refused_at < REFUSAL_COOLDOWN:
            return False
        uid = (job.get("metadata") or {}).get("uid", "")
        fresh = projection.status_record(row, failures, job_uid=uid)
        writes = projection.record_write(live, row, fresh)
        namespace = row["namespace"]
        for cm, force, manager in writes:  # in order: each builds on the last
            try:
                reader.apply_configmap(cm, force=force, manager=manager)
            except ApplyConflict:
                # `htrflow-campaigns apply` wrote the record since this
                # request read it, and its ending is the authoritative one.
                # Nothing is wrong with this service's grant, so the
                # namespace does not go into the cooldown below (2026-09-14
                # review). The next poll reads what it wrote.
                return True
            except Exception as e:  # noqa: BLE001 - any client error, same answer
                if namespace not in refused:
                    _LOG.warning("could not write %s: %s", cm["metadata"]["name"], e)
                refused[namespace] = time.monotonic()
                return True
        if writes:
            refused.pop(namespace, None)
        return bool(writes)

    def _campaign_configmaps() -> tuple[dict, dict]:
        """A campaign's two ConfigMaps, each by (namespace, campaign name):
        the converter's record (``volumes.txt``, provenance) and the status
        one this service writes beside it. One list call for the whole page
        rather than a get per campaign."""
        records: dict[tuple[str, str], dict] = {}
        statuses: dict[tuple[str, str], dict] = {}
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
        budget = RECORD_WRITES_PER_REQUEST
        for row, job in zip(rows, jobs):
            if budget <= 0:
                break
            status = statuses.get((row["namespace"], row["name"]))
            budget -= _record(row, job, status, None)
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

    def _serves(namespace: str, name: str) -> bool:
        """Whether this API could have a campaign by this name at all.

        Both halves go straight into an API path and into the ConfigMap
        names built from them, and the service is scoped to the namespaces
        it was given -- so a string no Kubernetes object could carry, or a
        namespace this API does not serve, is answered before any read
        rather than after one (2026-09-14 audit). Site-only mode has no
        `cfg` and no namespaces to compare against; its reader answers 503
        for everything and that is the honest answer there."""
        if not all(len(v) <= 63 and DNS_1123.fullmatch(v) for v in (namespace, name)):
            return False
        served = getattr(reader.cfg, "namespaces", ())
        return not served or namespace in served

    @app.api_route("/api/v1/jobs/{namespace}/{name}", methods=GET_HEAD)
    def get_job(
        namespace: str,
        name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict:
        if not _serves(namespace, name):
            raise HTTPException(status_code=404, detail="job not found")
        job = reader.get_job(namespace, name)
        # A Job that is not a campaign is no campaign's Job: the name is
        # answered as though it were absent, from a record or not at all.
        if job is None or not is_campaign(job):
            return _reaped_detail(namespace, name, offset, limit)
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
            cached_progress=progress.cached if progress is not None else None,
        )
        status_name = f"{cm_name or 'campaign-' + name}{projection.STATUS_SUFFIX}"
        live = reader.get_configmap(namespace, status_name)
        _record(body, job, live, body["failures"])
        return body

    def _reaped_detail(namespace: str, name: str, offset: int, limit: int) -> dict:
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
        return projection.record_detail(
            row,
            record,
            status,
            reader.cfg,
            pipe,
            offset,
            limit,
            fetch_progress=progress.fetch if progress is not None else None,
            cached_progress=progress.cached if progress is not None else None,
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
    static = Path(static_dir or DEFAULT_STATIC_DIR)
    if static.is_dir():
        site = BuiltSite(directory=static, html=True, viewer_csp=viewer_csp)
        app.mount("/", site, name="site")

    return app
