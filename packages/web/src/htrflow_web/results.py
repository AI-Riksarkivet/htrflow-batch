# packages/web/src/htrflow_web/results.py
"""``htrflow-results``: result files on the web front's origin, read from
the bucket with the logged-in user's own store keys (docs: security,
"Results"). No credentials of its own, no Kubernetes token."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Literal, Mapping

import boto3
import urllib3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    ReadTimeoutError,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .session import COOKIE, SessionCodec, SessionData, derive_keys

_LOG = logging.getLogger("htrflow_web.results")

#: Store answers that mean the keys themselves are wrong or revoked.
BAD_KEYS = {"InvalidAccessKeyId", "SignatureDoesNotMatch"}
#: Store answers that mean the keys work and the object is not readable/there.
NOT_THERE = {"404", "NoSuchKey", "NotFound"}
DENIED = {"403", "AccessDenied", "Forbidden"}
#: Login probe is a ranged GET, which carries an S3 error body. Only these
#: codes prove the keys work; a bare "403"/"400" (HEAD-style, no S3 code)
#: is not proof, so the login fails closed on it.
LOGIN_VALID = {"NoSuchKey", "NotFound", "404", "AccessDenied"}


class ResultsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    namespace: str = Field(alias="HTRFLOW_RESULTS_NAMESPACE", min_length=1)
    s3_endpoint: str = Field("", alias="S3_ENDPOINT")
    s3_bucket: str = Field(alias="S3_BUCKET", min_length=1)
    s3_verify_tls: bool = Field(True, alias="S3_VERIFY_TLS")
    session_key_file: str = Field(
        "/secrets/session/key", alias="HTRFLOW_SESSION_KEY_FILE"
    )
    session_hours: float = Field(8.0, alias="HTRFLOW_SESSION_HOURS", gt=0)
    key_derivation: Literal["hcp", "none"] = Field(
        "hcp", alias="HTRFLOW_KEY_DERIVATION"
    )
    trusted_hops: int = Field(1, alias="HTRFLOW_TRUSTED_HOPS", ge=0)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ResultsConfig":
        env = os.environ if env is None else env
        names = {f.alias for f in cls.model_fields.values()}
        return cls.model_validate({k: v for k, v in env.items() if k in names})


class ClientCache:
    """One S3 client per access key, least recently used first out."""

    def __init__(self, cfg: ResultsConfig, maxsize: int = 256) -> None:
        self._cfg, self._max = cfg, maxsize
        self._clients: OrderedDict[tuple[str, str], object] = OrderedDict()
        self._lock = threading.Lock()
        if not cfg.s3_verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def __len__(self) -> int:
        return len(self._clients)

    def build(self, access_key: str, secret_key: str):
        """A new, uncached client (own boto3 session: the default one is not
        thread-safe)."""
        return boto3.session.Session().client(
            "s3",
            endpoint_url=self._cfg.s3_endpoint or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            verify=None if self._cfg.s3_verify_tls else False,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def put(self, access_key: str, secret_key: str, client) -> None:
        with self._lock:
            self._clients[(access_key, secret_key)] = client
            self._clients.move_to_end((access_key, secret_key))
            while len(self._clients) > self._max:
                self._clients.popitem(last=False)

    def get(self, access_key: str, secret_key: str):
        k = (access_key, secret_key)
        with self._lock:
            if k in self._clients:
                self._clients.move_to_end(k)
                return self._clients[k]
        client = self.build(access_key, secret_key)
        self.put(access_key, secret_key, client)
        return client


class LoginLimiter:
    """Failed logins per key (address or user name) in a sliding window.
    Tracks at most ``max_keys`` keys, oldest evicted first."""

    def __init__(
        self, max_failures=5, window=60.0, clock=time.monotonic, max_keys=10_000
    ) -> None:
        self._max, self._window, self._clock = max_failures, window, clock
        self._max_keys = max_keys
        self._fails: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._fails)

    def _recent(self, key: str) -> deque[float] | None:
        q = self._fails.get(key)
        if q is None:
            return None
        cutoff = self._clock() - self._window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._fails[key]
            return None
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            q = self._recent(key)
            return q is not None and len(q) >= self._max

    def failed(self, key: str) -> None:
        with self._lock:
            q = self._recent(key)
            if q is None:
                q = self._fails[key] = deque()
                while len(self._fails) > self._max_keys:
                    self._fails.popitem(last=False)
            q.append(self._clock())

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def _code(e: ClientError) -> str:
    return str(e.response.get("Error", {}).get("Code", ""))


def _client_addr(request: Request, trusted_hops: int) -> str:
    """The address our own proxies saw: the ``trusted_hops``-th entry from the
    right of X-Forwarded-For (the left side is client-chosen)."""
    if trusted_hops >= 1:
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")
        ]
        if len(parts) >= trusted_hops and parts[-trusted_hops]:
            return parts[-trusted_hops]
    return request.client.host if request.client else ""


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    if fwd_proto and fwd_host:
        expected = f"{fwd_proto}://{fwd_host}"
    return bool(origin) and origin == expected


def _secure(request: Request) -> bool:
    fwd_proto = request.headers.get("x-forwarded-proto")
    if fwd_proto and request.headers.get("x-forwarded-host"):
        return fwd_proto == "https"
    return request.url.scheme == "https"


def session_of(request: Request, codec: SessionCodec) -> SessionData | None:
    token = request.cookies.get(COOKIE)
    return codec.open(token) if token else None


def _clear(response: Response, request: Request) -> None:
    # set_cookie with Max-Age=0 rather than delete_cookie: the web package's
    # read-only guard test bans every `.delete_*(` call in the source.
    response.set_cookie(
        COOKIE,
        "",
        max_age=0,
        expires=0,
        path="/",
        secure=_secure(request),
        httponly=True,
        samesite="strict",
    )


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


def create_results_app(
    cfg: ResultsConfig,
    codec: SessionCodec,
    clients: ClientCache | None = None,
    limiter: LoginLimiter | None = None,
) -> FastAPI:
    app = FastAPI()
    clients = clients or ClientCache(cfg)
    limiter = limiter or LoginLimiter()
    user_limiter = LoginLimiter(max_failures=10, window=300.0)
    app.state.cfg, app.state.codec, app.state.clients = cfg, codec, clients

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/results/_login")
    def login(body: Login, request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse(
                {"detail": "cross-origin login refused"}, status_code=403
            )
        addr = _client_addr(request, cfg.trusted_hops)
        if limiter.blocked(addr) or user_limiter.blocked(body.username):
            return JSONResponse(
                {"detail": "too many failed logins: wait a minute and try again"},
                status_code=429,
            )
        ak, sk = derive_keys(body.username, body.password, cfg.key_derivation)
        client = clients.build(ak, sk)
        try:
            got = client.get_object(
                Bucket=cfg.s3_bucket, Key=f"{cfg.namespace}/", Range="bytes=0-0"
            )
            stream = got.get("Body") if isinstance(got, dict) else None
            if stream is not None:
                stream.close()
        except ClientError as e:
            code = _code(e)
            if code in BAD_KEYS:
                limiter.failed(addr)
                user_limiter.failed(body.username)
                return JSONResponse(
                    {"detail": "the store did not accept that user name or password"},
                    status_code=401,
                )
            if code not in LOGIN_VALID:
                _LOG.warning(
                    "login probe: store answered %r (http %s)",
                    code,
                    e.response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                )
                return JSONResponse(
                    {"detail": "the result store answered without a reason"},
                    status_code=502,
                )
        except (ConnectTimeoutError, ReadTimeoutError):
            return JSONResponse(
                {"detail": "the result store timed out"}, status_code=504
            )
        except BotoCoreError as e:
            _LOG.warning("login probe: %s", e)
            return JSONResponse(
                {"detail": "the result store could not be reached"}, status_code=502
            )
        clients.put(ak, sk, client)
        limiter.succeeded(addr)
        user_limiter.succeeded(body.username)
        response = Response(status_code=204)
        response.set_cookie(
            COOKIE,
            codec.seal(body.username, ak, sk),
            max_age=int(cfg.session_hours * 3600),
            path="/",
            secure=_secure(request),
            httponly=True,
            samesite="strict",
        )
        return response

    @app.post("/results/_logout")
    def logout(request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse(
                {"detail": "cross-origin logout refused"}, status_code=403
            )
        response = Response(status_code=204)
        _clear(response, request)
        return response

    @app.get("/results/_session")
    def session(request: Request) -> Response:
        data = session_of(request, codec)
        if data is None:
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        return JSONResponse({"user": data.user})

    return app
