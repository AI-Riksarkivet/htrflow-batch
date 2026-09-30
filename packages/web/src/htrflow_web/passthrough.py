"""/results on the web front's origin, passed through to the results proxy:
one path in every install mode (docs: security, "Results"). This pod reads
nothing from the answer and cannot open the session cookie it forwards."""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

_LOG = logging.getLogger("htrflow_web.passthrough")

_UP = ("cookie", "origin", "content-type", "if-none-match", "if-modified-since")
_DOWN = (
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "cache-control",
    "content-security-policy",
    "x-content-type-options",
    "content-disposition",
    "www-authenticate",
)
_PREFIX = b"/results/"


def results_route(
    app: FastAPI, proxy_base: str, client: httpx.AsyncClient | None = None
) -> None:
    base = proxy_base.rstrip("/")
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0))

    @app.api_route("/results/{path:path}", methods=["GET", "HEAD", "POST"])
    async def results(request: Request):
        # Only the raw path is trusted: the decoded one would turn %2F into a
        # slash the proxy's key checks are meant to see. Forwarded as received.
        raw = request.scope.get("raw_path") or b""
        raw = raw.split(b"?", 1)[0]
        if not raw.startswith(_PREFIX):
            return JSONResponse({"detail": "not found"}, status_code=404)
        tail = raw[len(_PREFIX) :].decode("latin-1")
        query = request.scope.get("query_string", b"").decode("latin-1")
        url = f"{base}/{tail}" + (f"?{query}" if query else "")

        headers = {k: v for k, v in request.headers.items() if k in _UP}
        # The body is passed as stored; never negotiate a coding this pod
        # would then have to strip the header for.
        headers["accept-encoding"] = "identity"
        peer = request.client.host if request.client else ""
        prior = request.headers.get("x-forwarded-for")
        headers["x-forwarded-for"] = f"{prior}, {peer}" if prior else peer
        headers["x-forwarded-proto"] = request.headers.get(
            "x-forwarded-proto", request.url.scheme
        )
        headers["x-forwarded-host"] = request.headers.get(
            "x-forwarded-host", request.headers.get("host", "")
        )
        body = await request.body() if request.method == "POST" else None
        try:
            upstream = await client.send(
                client.build_request(
                    request.method, url, headers=headers, content=body
                ),
                stream=True,
            )
        except httpx.HTTPError as e:
            _LOG.warning("results proxy did not answer: %s", e)
            return JSONResponse(
                {"detail": "the results service did not answer"}, status_code=502
            )

        raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in upstream.headers.multi_items()
            if k.lower() in _DOWN or k.lower() == "set-cookie"
        ]
        if request.method == "HEAD" or upstream.status_code in (204, 304):
            await upstream.aclose()
            response = Response(status_code=upstream.status_code)
            response.raw_headers = raw_headers
            return response

        async def body_iter():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        response = StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            background=BackgroundTask(upstream.aclose),
        )
        response.raw_headers = raw_headers
        return response
