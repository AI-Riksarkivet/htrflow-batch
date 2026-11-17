"""The routes, the one guard in front of every ``/v1`` route, and the error
shape: ``{"detail": "<what was refused>", "hint": "<what to do>"}``."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from htrflow_api import MIN_CLIENT
from htrflow_api.config import ApiConfig
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
_BROKEN = ("internal error", "Try again; if it stays, tell whoever runs the cluster.")


def _numbers(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")[:3]
    if len(parts) < 3 or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def too_old(client: str, minimum: str = MIN_CLIENT) -> bool:
    """Whether ``client`` is a readable version below ``minimum``. A version
    that does not start with three numbers is not too old."""
    have, need = _numbers(client), _numbers(minimum)
    return have is not None and need is not None and have < need


def _refusal(status: int, detail: str, hint: str, headers=None) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "hint": hint}, status_code=status, headers=headers
    )


def create_app(cfg: ApiConfig, verifier: Verifier) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    v1 = f"{cfg.path}/v1"

    def check_client(request: Request) -> None:
        client = request.headers.get(CLIENT_HEADER, "")
        if client and too_old(client):
            raise Refused(
                426,
                f"This htrflow-campaigns is version {client} and the server "
                f"needs at least {MIN_CLIENT}.",
                "Upgrade htrflow-campaigns, then try again.",
            )

    def current_identity(request: Request) -> Identity:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Refused(401, _NO_TOKEN, _RE_LOGIN)
        return verifier.verify(token.strip())

    # Every route that needs a person is declared with `guarded.get(...)`:
    # the version check and the token check are applied there, not repeated
    # per route. Routes sit directly on the app (not in an included router).
    class guarded:  # noqa: N801 - reads as a decorator namespace
        @staticmethod
        def get(path: str):
            return app.get(
                v1 + path,
                dependencies=[Depends(check_client), Depends(current_identity)],
            )

    @app.get("/healthz")
    def healthz() -> dict:
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

    @guarded.get("/whoami")
    def whoami(who: Identity = Depends(current_identity)) -> dict:
        return {
            "user": who.user,
            "groups": list(who.groups),
            "namespace": cfg.namespace,
            "queue": cfg.queue,
            "serverVersion": cfg.batch_version,
        }

    def under_v1(request: Request) -> bool:
        path = request.url.path
        return path == v1 or path.startswith(v1 + "/")

    def respond(request: Request, status: int, detail: str, hint: str, headers=None):
        resp = _refusal(status, detail, hint, headers)
        if under_v1(request):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(Refused)
    async def refused(request: Request, exc: Refused):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return respond(request, exc.status, exc.detail, exc.hint, headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        detail, hint = _WRONG_METHOD if exc.status_code == 405 else _NOT_FOUND
        if exc.status_code not in (404, 405):
            detail, hint = _BROKEN
        return respond(request, exc.status_code, detail, hint, exc.headers)

    @app.exception_handler(Exception)
    async def broken(request: Request, exc: Exception):
        # No headers in the log line: they carry the token.
        log.error(
            "unhandled error on %s %s", request.method, request.url.path,
            exc_info=exc,
        )  # fmt: skip
        return respond(request, 500, *_BROKEN)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        resp = await call_next(request)
        if under_v1(request):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
