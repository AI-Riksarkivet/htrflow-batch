"""The routes, the one guard in front of every ``/v1`` route, and the error
shape: ``{"detail": "<what was refused>", "hint": "<what to do>"}``."""

from __future__ import annotations

import logging
import traceback

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from htrflow_api import MIN_CLIENT
from htrflow_api.config import ApiConfig, checked_prefix
from htrflow_api.tokens import Identity, Refused, Verifier

#: The CLI's htrflow-converter version.
CLIENT_HEADER = "X-Htrflow-Client"

log = logging.getLogger("htrflow_api")

_NO_TOKEN = "This call needs a sign-in token."
_RE_LOGIN = "Sign in again: htrflow-campaigns login"
_NOT_FOUND = ("There is nothing at this address.", "Check the address and try again.")
_WRONG_METHOD = (
    "This address does not answer this kind of request.",
    "Check the address and try again.",
)
_BAD_REQUEST = (
    "This request could not be understood.",
    "Check it and try again.",
)
_BROKEN = ("internal error", "Try again; if it stays, tell whoever runs the cluster.")
_INVALID = (
    "The request is not what this call takes.",
    "Upgrade htrflow-campaigns; if it stays, tell whoever runs the cluster.",
)


def _numbers(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")[:3]
    # Nine digits at most: a longer part is unreadable, not a huge number.
    if len(parts) < 3 or not all(
        p.isascii() and p.isdigit() and len(p) <= 9 for p in parts
    ):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def too_old(client: str, minimum: str = MIN_CLIENT) -> bool:
    """Whether ``client`` is a readable version below ``minimum``. A version
    that does not start with three numbers of up to nine digits is not
    too old."""
    have, need = _numbers(client), _numbers(minimum)
    return have is not None and need is not None and have < need


def _refusal(status: int, detail: str, hint: str, headers=None) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "hint": hint}, status_code=status, headers=headers
    )


_root_path_logged = False


def _log_broken(scope: Scope, exc: Exception) -> None:
    """The exception's type and the file and line it was raised at, never
    its message or arguments (they may hold a token), nor any header."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "?"
    log.error(
        "unhandled %s at %s on %s %s",
        type(exc).__name__, where, scope.get("method", "-"), scope.get("path", ""),
    )  # fmt: skip


def route_path(scope: Scope) -> str:
    """The path the router matches: ``path`` with ``root_path`` removed, as
    Starlette's ``get_route_path`` does."""
    path, root = scope["path"], scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    return path[len(root) :] if path[len(root)] == "/" else path


class _Guard:
    """Runs before routing, for every request at ``v1`` or under ``v1/``,
    whatever the method and however the route was registered (decorator,
    router, mount): the version check, then the token check, then the route.
    The one exception is ``GET`` on ``v1/login``. Fail-closed: a
    route added later is guarded without anyone remembering to ask for it."""

    def __init__(self, app: ASGIApp, v1: str, verifier: Verifier) -> None:
        self.app, self.v1, self.verifier = app, v1, verifier

    def under_v1(self, path: str) -> bool:
        return path == self.v1 or path.startswith(self.v1 + "/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Every request, guarded; an exception from anywhere below ends here,
        logged by `_log_broken` and answered 500, and goes no further: the
        server would log it with its message, and the message may hold a
        token."""
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        started = False

        async def watched(message) -> None:
            nonlocal started
            started = started or message["type"] in (
                "http.response.start", "websocket.accept", "websocket.close",
            )  # fmt: skip
            await send(message)

        try:
            await self._guarded(scope, receive, watched)
        except Exception as exc:  # noqa: BLE001 - logged without its text
            _log_broken(scope, exc)
            if started:  # nothing more can be said on this response
                return
            if scope["type"] != "http":
                return await send({"type": "websocket.close", "code": 1011})
            v1 = self.under_v1(route_path(scope))
            headers = {"Cache-Control": "no-store"} if v1 else None
            await _refusal(500, *_BROKEN, headers)(scope, receive, send)

    async def _guarded(self, scope: Scope, receive: Receive, send: Send) -> None:
        global _root_path_logged
        if scope.get("root_path"):
            # This service has its own prefix setting and no use for ASGI
            # root_path; refuse everything, /healthz too, so a pod started
            # that way never turns ready.
            if not _root_path_logged:
                _root_path_logged = True
                log.error(
                    "started with a root path (%r), which this service does "
                    "not support; use HTRFLOW_API_PATH",
                    scope["root_path"],
                )
            if scope["type"] != "http":
                return await send({"type": "websocket.close", "code": 1011})
            return await _refusal(500, *_BROKEN)(scope, receive, send)
        path = route_path(scope)  # the value the router will match
        if not self.under_v1(path):
            return await self.app(scope, receive, send)
        if scope["type"] != "http":  # nothing here speaks websocket
            return await send({"type": "websocket.close", "code": 1008})

        async def no_store(message) -> None:
            if message["type"] == "http.response.start":
                kept = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"cache-control"
                ]
                message["headers"] = [*kept, (b"cache-control", b"no-store")]
            await send(message)

        request = Request(scope, receive)
        open_door = scope["method"] == "GET" and path == self.v1 + "/login"
        if not open_door:
            try:
                check_client(request)
                identity = await run_in_threadpool(
                    self.verifier.verify, _token(request)
                )
            except Refused as e:
                return await _refused(e)(scope, receive, no_store)
            scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, no_store)


def _token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Refused(401, _NO_TOKEN, _RE_LOGIN)
    return token.strip()


def check_client(request: Request) -> None:
    client = request.headers.get(CLIENT_HEADER, "")
    if client and too_old(client):
        raise Refused(
            426,
            f"This htrflow-campaigns is version {client} and the server "
            f"needs at least {MIN_CLIENT}.",
            "Upgrade htrflow-campaigns, then try again.",
        )


def _refused(exc: Refused) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return _refusal(exc.status, exc.detail, exc.hint, headers)


def current_identity(request: Request) -> Identity:
    """The verified caller, for a route to declare with ``Depends``. The guard
    sets it before routing; reaching here without it is a bug, answered 500."""
    identity = request.scope.get("state", {}).get("identity")
    if not isinstance(identity, Identity):
        raise RuntimeError("route reached without the guard having run")
    return identity


def create_app(cfg: ApiConfig, verifier: Verifier) -> FastAPI:
    # Again here: model_copy and model_construct skip the validators.
    v1 = f"{checked_prefix(cfg.path)}/v1"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict:  # on the loop, never queued behind verify()
        return {"ok": True}

    @app.get(f"{v1}/login")
    def login() -> dict:
        return {
            "issuer": cfg.issuer,
            "clientId": cfg.client_id,
            "audience": cfg.audience,
            "serverVersion": cfg.batch_version,
            "minClient": MIN_CLIENT,
        }

    @app.get(f"{v1}/whoami")
    def whoami(who: Identity = Depends(current_identity)) -> dict:
        return {
            "user": who.user,
            "groups": list(who.groups),
            "namespace": cfg.namespace,
            "queue": cfg.queue,
            "serverVersion": cfg.batch_version,
        }

    @app.exception_handler(Refused)
    async def refused(request: Request, exc: Refused):
        return _refused(exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail, hint = {404: _NOT_FOUND, 405: _WRONG_METHOD}.get(
            exc.status_code, _BAD_REQUEST if exc.status_code < 500 else _BROKEN
        )
        return _refusal(exc.status_code, detail, hint, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return _refusal(422, *_INVALID)  # never the input: it is the caller's

    app.add_middleware(_Guard, v1=v1, verifier=verifier)
    return app
