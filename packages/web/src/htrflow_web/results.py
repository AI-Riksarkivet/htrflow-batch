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
from datetime import timezone
from email.utils import format_datetime, parsedate_to_datetime
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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from .cookie import browser_cookie, forwarded, session_token
from .results_rules import FILE_HEADERS, allowed_key, served_type
from .session import SessionCodec, SessionData, derive_keys

_LOG = logging.getLogger("htrflow_web.results")

#: Store answers that mean the keys themselves are wrong or revoked.
BAD_KEYS = {"InvalidAccessKeyId", "SignatureDoesNotMatch"}
#: Store answers that mean the keys work and the object is not readable/there.
NOT_THERE = {"404", "NoSuchKey", "NotFound"}
DENIED = {"403", "AccessDenied", "Forbidden"}
#: Login probe is a ranged GET, which carries an S3 error body. Only these
#: codes prove the keys work; a bare "403"/"400" (HEAD-style, no S3 code)
#: is not proof, so the login fails closed on it.
LOGIN_VALID = {"NoSuchKey", "NotFound", "404", "AccessDenied", "InvalidRange"}


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
            self._fails.move_to_end(key)
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
    scheme, host = forwarded(request.headers, request.url.scheme)
    return bool(origin) and origin == f"{scheme}://{host}"


def _secure(request: Request) -> bool:
    return forwarded(request.headers, request.url.scheme)[0] == "https"


def session_of(request: Request, codec: SessionCodec) -> SessionData | None:
    token = session_token(request)
    return codec.open(token) if token else None


def _fail(detail: str, status: int) -> JSONResponse:
    """A file-route error: never cacheable, never sniffed."""
    return JSONResponse(
        {"detail": detail},
        status_code=status,
        headers={
            "Cache-Control": FILE_HEADERS["Cache-Control"],
            "X-Content-Type-Options": FILE_HEADERS["X-Content-Type-Options"],
        },
    )


def _clear(response: Response, request: Request) -> None:
    response.delete_cookie(
        browser_cookie(request.headers, request.url.scheme),
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
        if limiter.blocked(addr):
            return JSONResponse(
                {
                    "detail": "too many failed logins from this address: "
                    "wait a minute and try again"
                },
                status_code=429,
            )
        if user_limiter.blocked(body.username):
            return JSONResponse(
                {
                    "detail": "too many failed logins for this user: "
                    "wait five minutes and try again"
                },
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
            browser_cookie(request.headers, request.url.scheme),
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

    @app.api_route("/results/{path:path}", methods=["GET", "HEAD"])
    def file(path: str, request: Request) -> Response:
        data = session_of(request, codec)
        if data is None:
            return _fail("not logged in", 401)
        # Only the raw path: a server-decoded %2F must not be decoded again.
        raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
        if not raw_path.startswith(b"/results/"):
            return _fail("not found", 404)
        key = allowed_key(
            raw_path[len(b"/results/") :].decode("latin-1"), cfg.namespace
        )
        if key is None:
            return _fail("not found", 404)
        args = {"Bucket": cfg.s3_bucket, "Key": key}
        if inm := request.headers.get("if-none-match"):
            args["IfNoneMatch"] = inm
        if ims := request.headers.get("if-modified-since"):
            try:  # an unparsable date is ignored (RFC 9110)
                args["IfModifiedSince"] = parsedate_to_datetime(ims)
            except (TypeError, ValueError):
                pass
        s3 = clients.get(data.access_key, data.secret_key)
        head = request.method == "HEAD"
        try:
            obj = (s3.head_object if head else s3.get_object)(**args)
        except ClientError as e:
            code = _code(e)
            if code in ("304", "NotModified"):
                meta = e.response.get("ResponseMetadata", {})
                got = meta.get("HTTPHeaders", {})
                kept = {
                    n: got[k]
                    for n, k in (("ETag", "etag"), ("Last-Modified", "last-modified"))
                    if got.get(k)
                }
                return Response(status_code=304, headers={**kept, **FILE_HEADERS})
            if code in NOT_THERE:
                return _fail("not found", 404)
            # A HEAD error has no body, so a bad or revoked key surfaces as a
            # bare "403" here and maps to 403; only GET can tell 401 apart.
            if code in DENIED:
                return _fail("your account may not read this", 403)
            if code in BAD_KEYS:
                r = _fail("your login is no longer accepted", 401)
                _clear(r, request)
                return r
            _LOG.warning("store answered %s for %s", code, key)
            return _fail("the result store failed", 502)
        except (ConnectTimeoutError, ReadTimeoutError):
            return _fail("the result store timed out", 504)
        except BotoCoreError as e:
            _LOG.warning("store unreachable: %s", e)
            return _fail("the result store could not be reached", 502)
        ctype, attachment = served_type(obj.get("ContentType"))
        # Content-Type set verbatim (media_type= would append a charset).
        headers = {
            **FILE_HEADERS,
            "Content-Type": ctype,
            "Content-Length": str(obj["ContentLength"]),
        }
        if etag := obj.get("ETag"):
            headers["ETag"] = etag
        if lm := obj.get("LastModified"):
            headers["Last-Modified"] = format_datetime(
                lm.astimezone(timezone.utc), usegmt=True
            )
        if attachment:
            headers["Content-Disposition"] = "attachment"
        if head:
            return Response(status_code=200, headers=headers)
        body = obj["Body"]

        def chunks():
            # finally: closes on a client disconnect too, where Starlette
            # cancels the response and skips the background task.
            try:
                yield from body.iter_chunks(1024 * 1024)
            finally:
                body.close()

        return StreamingResponse(
            chunks(),
            headers=headers,
            background=BackgroundTask(body.close),
        )

    return app
