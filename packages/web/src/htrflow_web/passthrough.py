"""/results on the web front's origin, passed through to the results proxy:
one path in every install mode (docs: security, "Results"). This pod reads
nothing from the answer and cannot open the session cookie it forwards."""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .cookie import COOKIE, forwarded, no_cookie_jar

_LOG = logging.getLogger("htrflow_web.passthrough")

_UP = ("origin", "content-type", "if-none-match", "if-modified-since")
_DOWN = (
    "content-type",
    "content-length",
    "etag",
    "last-modified",
    "cache-control",
    "content-security-policy",
    "x-content-type-options",
    "content-disposition",
    "content-encoding",
    "www-authenticate",
)
_PREFIX = b"/results/"
_MAX_BODY = 16 * 1024


def proxy_client(
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """One client for everyone's requests, so it must keep no cookies: the
    login's Set-Cookie would otherwise ride along on every later request
    that arrives without one (cookie.no_cookie_jar)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=60.0),
        cookies=no_cookie_jar(),
        transport=transport,
    )


def results_route(
    app: FastAPI, proxy_base: str, client: httpx.AsyncClient | None = None
) -> None:
    base = proxy_base.rstrip("/")
    client = client or proxy_client()

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

        # Bytes end to end: Starlette decodes header values as latin-1 and httpx
        # encodes str values as ASCII, so a str round trip can raise.
        headers: dict[bytes, bytes] = {
            k: v for k, v in request.headers.raw if k.decode("latin-1") in _UP
        }
        # The body is passed as stored; never negotiate a coding this pod
        # would then have to strip the header for.
        headers[b"accept-encoding"] = b"identity"
        peer = request.client.host if request.client else ""
        prior = request.headers.get("x-forwarded-for")
        headers[b"x-forwarded-for"] = (f"{prior}, {peer}" if prior else peer).encode(
            "latin-1"
        )
        proto, host = forwarded(request.headers, request.url.scheme)
        headers[b"x-forwarded-proto"] = proto.encode("latin-1")
        headers[b"x-forwarded-host"] = host.encode("latin-1")
        cookie = request.cookies.get(COOKIE)
        if cookie is not None:
            headers[b"cookie"] = f"{COOKIE}={cookie}".encode("utf-8", "replace")
        body = None
        if request.method == "POST":
            too_big = JSONResponse({"detail": "body too large"}, status_code=413)
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > _MAX_BODY:
                return too_big
            parts: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > _MAX_BODY:
                    return too_big
                parts.append(chunk)
            body = b"".join(parts)
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
            (k, v)
            for k, v in upstream.headers.raw
            if k.decode("latin-1").lower() in _DOWN or k.lower() == b"set-cookie"
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
            except httpx.HTTPError as e:
                _LOG.warning("results proxy broke off mid-answer: %s", type(e).__name__)
                raise
            finally:
                await upstream.aclose()

        response = StreamingResponse(
            body_iter(),
            status_code=upstream.status_code,
            background=BackgroundTask(upstream.aclose),
        )
        response.raw_headers = raw_headers
        return response
