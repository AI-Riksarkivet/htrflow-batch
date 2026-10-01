# Results Behind a Login — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve result files through a new proxy pod that reads the bucket with each logged-in user's own store keys, and put the web front and viewer behind that login.

**Architecture:** A second entrypoint `htrflow-results` in `packages/web` (same image) is a small FastAPI app: login/logout/session endpoints that keep derived S3 keys in an AES-GCM-encrypted cookie, and `GET/HEAD /results/<key>` that streams objects with the session's keys. The web front passes `/results/*` through to it, asks it whether a request's cookie is a session before answering `/api/v1/*`, and reads progress through it with the caller's cookie. The chart adds the proxy's Deployment/Service/NetworkPolicy; the dev stacks drop anonymous read and create a login user.

**Tech Stack:** Python 3.10+, FastAPI, httpx, boto3, cryptography (AESGCM), SvelteKit frontend (vitest), Helm, Kyverno policies, moto for S3 tests.

**Spec:** `docs/superpowers/specs/2026-09-30-results-proxy-login-design.md`

## Global Constraints

- The proxy holds no bucket credentials of its own and no Kubernetes token (`automountServiceAccountToken: false`, no Role).
- The web front never decrypts the session cookie and never holds a session key or S3 key.
- Cookie `htr_session`: AES-GCM, 256-bit key from `results.sessionSecret` (Secret key `key`, 32 random bytes base64), random 12-byte nonce, `HttpOnly; Secure; SameSite=Strict; Path=/`, expiry `results.sessionHours` (default 8). `Secure` is omitted only when the request arrived over plain HTTP.
- The cookie holds the derived access key, secret key, username and expiry — never the password.
- Key derivation `hcp`: access key = base64(username), secret key = hex MD5(password). `none`: the username is the access key and the password the secret key.
- Allowed keys: `<namespace>/…` (the release namespace) or `status/logs/…`; refused before S3 when a segment is empty, `.`, `..`, contains a backslash, or the raw path had an encoded slash (`%2f`/`%5c`, any case).
- Content types passed as stored: `application/json`, `application/xml`, `text/xml`, `text/plain` (with parameters). Anything else → `application/octet-stream` + `Content-Disposition: attachment`.
- Every file answer: `Cache-Control: private, no-cache`, `Content-Security-Policy: default-src 'none'; sandbox`, `X-Content-Type-Options: nosniff`.
- S3 client: signature v4, path-style addressing, `request_checksum_calculation`/`response_checksum_validation` = `when_required`, `verify` from `S3_VERIFY_TLS`, connect 5 s, read 30 s, `retries={"max_attempts": 2, "mode": "standard"}`.
- Login failures: 5 per client address per 60 s, then `429` for 60 s; in memory per replica.
- Web session-check cache: 30 s per cookie value, at most 1024 entries.
- Proxy port 8082, Service `htrflow-results`. The web front's internal progress base is `http://htrflow-results:8082/results`.
- Charts: `htrflow-batch` 0.16.0, `htrflow-devstack` 0.5.0. Release v0.9.0 (separate, after merge).
- Site docs: no names, dates, versions, hosts, hardware or story ids (`scripts/docs_lint.py`).
- Run `make ci` and `scripts/loc-budget.sh` before every push; commit one logical step at a time; no Co-Authored-By trailers.

## Review Focus

- A cookie from before a session-key rotation (or a tampered one) must read as "no session" (`401`), never as a 500 — Task 1.
- A user's keys revoked or password changed on the store mid-session: the next file read returns `401` and clears the cookie — Task 4.
- Two users polling the same campaign: user B must not see progress cached from user A's read — Task 7.
- The pass-through must not buffer a large object in the web pod's memory, and must pass `304` and `Set-Cookie` through unchanged — Task 8.
- A `POST /results/_login` from another origin (no or foreign `Origin` header) is refused with `403` even with valid credentials — Task 3.

---

### Task 1: Session cookie and key derivation

**Files:**
- Create: `packages/web/src/htrflow_web/session.py`
- Modify: `packages/web/pyproject.toml` (dependencies)
- Test: `packages/web/tests/test_session.py`

**Interfaces:**
- Produces:
  - `SessionData(user: str, access_key: str, secret_key: str, expires: float)` — frozen dataclass.
  - `SessionCodec(key: bytes, hours: float, clock: Callable[[], float] = time.time)` with `seal(user, access_key, secret_key) -> str` and `open(token: str) -> SessionData | None`.
  - `derive_keys(username: str, password: str, mode: Literal["hcp", "none"]) -> tuple[str, str]`.
  - `load_key(path: str) -> bytes` — reads a base64 file, requires exactly 32 bytes, raises `ValueError` otherwise.
  - `COOKIE = "htr_session"`.

- [ ] **Step 1: Add dependencies**

In `packages/web/pyproject.toml` `dependencies`, after `httpx`:

```toml
    # htrflow-results (results.py) reads result files with each logged-in
    # user's own store keys, and seals those keys into the session cookie.
    "boto3>=1.35",
    "cryptography>=43",
```

Run: `uv lock && uv sync --locked --all-packages`
Expected: lock updated, no errors.

- [ ] **Step 2: Write the failing tests**

```python
# packages/web/tests/test_session.py
import base64
import hashlib

import pytest

from htrflow_web.session import COOKIE, SessionCodec, derive_keys, load_key

KEY = bytes(range(32))


def test_a_sealed_session_opens_to_what_went_in():
    codec = SessionCodec(KEY, hours=8, clock=lambda: 1000.0)
    token = codec.seal("anna", "AK", "SK")
    data = codec.open(token)
    assert (data.user, data.access_key, data.secret_key) == ("anna", "AK", "SK")
    assert data.expires == 1000.0 + 8 * 3600


def test_the_password_is_never_in_the_cookie():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    assert b"SK" not in base64.urlsafe_b64decode(token + "==")


def test_an_expired_session_is_no_session():
    now = [1000.0]
    codec = SessionCodec(KEY, hours=1, clock=lambda: now[0])
    token = codec.seal("anna", "AK", "SK")
    now[0] += 3601
    assert codec.open(token) is None


@pytest.mark.parametrize("token", ["", "not-base64!!", "AAAA", "A" * 400])
def test_garbage_is_no_session(token):
    assert SessionCodec(KEY, hours=8).open(token) is None


def test_a_tampered_cookie_is_no_session():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    raw = bytearray(base64.urlsafe_b64decode(token + "=="))
    raw[-1] ^= 1
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    assert SessionCodec(KEY, hours=8).open(tampered) is None


def test_a_cookie_under_another_key_is_no_session():
    token = SessionCodec(KEY, hours=8).seal("anna", "AK", "SK")
    assert SessionCodec(bytes(32), hours=8).open(token) is None


def test_hcp_keys_are_base64_user_and_md5_password():
    ak, sk = derive_keys("anna", "p\\ss", "hcp")
    assert ak == base64.b64encode(b"anna").decode()
    assert sk == hashlib.md5(b"p\\ss").hexdigest()


def test_no_derivation_takes_the_keys_as_given():
    assert derive_keys("AKID", "SECRET", "none") == ("AKID", "SECRET")


def test_the_key_file_must_hold_32_bytes(tmp_path):
    good = tmp_path / "good"
    good.write_text(base64.b64encode(KEY).decode() + "\n")
    assert load_key(str(good)) == KEY
    short = tmp_path / "short"
    short.write_text(base64.b64encode(b"x" * 16).decode())
    with pytest.raises(ValueError, match="32 bytes"):
        load_key(str(short))


def test_the_cookie_name():
    assert COOKIE == "htr_session"
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_session.py`
Expected: FAIL, `ModuleNotFoundError: htrflow_web.session`.

- [ ] **Step 4: Implement**

```python
# packages/web/src/htrflow_web/session.py
"""The results proxy's session: the logged-in user's store keys, sealed into
the ``htr_session`` cookie with AES-GCM. Never the password; never readable
by JavaScript (the cookie is HttpOnly) or by the web front (it has no key).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

COOKIE = "htr_session"
_NONCE = 12


@dataclass(frozen=True)
class SessionData:
    user: str
    access_key: str
    secret_key: str
    expires: float


def load_key(path: str) -> bytes:
    with open(path, encoding="ascii") as f:
        key = base64.b64decode(f.read().strip())
    if len(key) != 32:
        raise ValueError(f"{path}: the session key must be 32 bytes, got {len(key)}")
    return key


def derive_keys(
    username: str, password: str, mode: Literal["hcp", "none"]
) -> tuple[str, str]:
    """HCP's S3 keys are derived from the account: base64 of the user name,
    hex MD5 of the password. A store that issues keys takes them as given."""
    if mode == "none":
        return username, password
    access = base64.b64encode(username.encode()).decode()
    secret = hashlib.md5(password.encode(), usedforsecurity=False).hexdigest()
    return access, secret


class SessionCodec:
    def __init__(
        self, key: bytes, hours: float, clock: Callable[[], float] = time.time
    ) -> None:
        self._aead = AESGCM(key)
        self._ttl = hours * 3600
        self._clock = clock

    def seal(self, user: str, access_key: str, secret_key: str) -> str:
        body = json.dumps(
            {"u": user, "a": access_key, "s": secret_key, "e": self._clock() + self._ttl}
        ).encode()
        nonce = os.urandom(_NONCE)
        sealed = nonce + self._aead.encrypt(nonce, body, None)
        return base64.urlsafe_b64encode(sealed).decode().rstrip("=")

    def open(self, token: str) -> SessionData | None:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            body = self._aead.decrypt(raw[:_NONCE], raw[_NONCE:], None)
            d = json.loads(body)
            data = SessionData(d["u"], d["a"], d["s"], float(d["e"]))
        except (binascii.Error, ValueError, InvalidTag, KeyError, TypeError):
            return None
        return data if data.expires > self._clock() else None
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run --no-sync pytest -q packages/web/tests/test_session.py`
Expected: PASS (11 tests). If `raw[:_NONCE]` on a short token raises `ValueError` from AESGCM ("nonce must be…"), it is caught above.

- [ ] **Step 6: Commit**

```bash
git add packages/web/pyproject.toml uv.lock packages/web/src/htrflow_web/session.py packages/web/tests/test_session.py
git commit -m "feat(web): the results session -- store keys sealed with AES-GCM, never the password"
```

---

### Task 2: Key and content-type rules

**Files:**
- Create: `packages/web/src/htrflow_web/results_rules.py`
- Test: `packages/web/tests/test_results_rules.py`

**Interfaces:**
- Produces:
  - `allowed_key(raw_path: str, namespace: str) -> str | None` — `raw_path` is the path after `/results/` exactly as received (still percent-encoded); returns the decoded key or `None`.
  - `served_type(stored: str | None) -> tuple[str, bool]` — `(content_type, attachment)`.
  - `FILE_HEADERS: dict[str, str]` — the three fixed security/cache headers.

- [ ] **Step 1: Write the failing tests**

```python
# packages/web/tests/test_results_rules.py
import pytest

from htrflow_web.results_rules import FILE_HEADERS, allowed_key, served_type

NS = "htrflow-batch"


@pytest.mark.parametrize(
    "raw,key",
    [
        ("htrflow-batch/demo-v1/R1/iiif.json", "htrflow-batch/demo-v1/R1/iiif.json"),
        ("htrflow-batch/demo-v1/R%201/alto/1.xml", "htrflow-batch/demo-v1/R 1/alto/1.xml"),
        ("status/logs/demo-v1/R1.txt", "status/logs/demo-v1/R1.txt"),
        ("htrflow-batch/sources/demo-v1/R1/manifest.json",
         "htrflow-batch/sources/demo-v1/R1/manifest.json"),
    ],
)
def test_result_keys_are_allowed(raw, key):
    assert allowed_key(raw, NS) == key


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "other-ns/demo-v1/R1/iiif.json",
        "htrflow-batch",
        "htrflow-batch/",
        "htrflow-batch//R1/iiif.json",
        "htrflow-batch/./R1",
        "htrflow-batch/../other/x",
        "htrflow-batch/a%2F..%2Fb",
        "htrflow-batch/a%2fb",
        "htrflow-batch/a%5Cb",
        "htrflow-batch/a\\b",
        "status/logsX/a",
        "status/other/a",
        "htrflow-batch/%2e%2e/x",
    ],
)
def test_anything_else_is_refused(raw):
    assert allowed_key(raw, NS) is None


@pytest.mark.parametrize(
    "stored,served",
    [
        ("application/json", ("application/json", False)),
        ("application/xml", ("application/xml", False)),
        ("text/xml; charset=utf-8", ("text/xml; charset=utf-8", False)),
        ("text/plain; charset=utf-8", ("text/plain; charset=utf-8", False)),
        ("text/html", ("application/octet-stream", True)),
        ("image/svg+xml", ("application/octet-stream", True)),
        ("TEXT/HTML", ("application/octet-stream", True)),
        (None, ("application/octet-stream", True)),
        ("", ("application/octet-stream", True)),
    ],
)
def test_only_the_types_the_wrapper_writes_pass_as_stored(stored, served):
    assert served_type(stored) == served


def test_the_fixed_headers():
    assert FILE_HEADERS == {
        "Cache-Control": "private, no-cache",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_rules.py`
Expected: FAIL, module missing.

- [ ] **Step 3: Implement**

```python
# packages/web/src/htrflow_web/results_rules.py
"""What the results proxy may serve, and how (docs: security, "Results")."""

from __future__ import annotations

import re
from urllib.parse import unquote

FILE_HEADERS = {
    "Cache-Control": "private, no-cache",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

_PASSED = ("application/json", "application/xml", "text/xml", "text/plain")
_ENCODED_SEPARATOR = re.compile(r"%(2f|5c)", re.IGNORECASE)


def allowed_key(raw_path: str, namespace: str) -> str | None:
    """The object key for a request path, or None when it is not a result
    key. Decoded exactly once; an encoded separator is refused outright, so
    a decoded key can never gain a segment the raw path did not show."""
    if _ENCODED_SEPARATOR.search(raw_path):
        return None
    key = unquote(raw_path)
    if "\\" in key:
        return None
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if len(parts) >= 2 and parts[0] == namespace:
        return key
    if len(parts) >= 3 and parts[:2] == ["status", "logs"]:
        return key
    return None


def served_type(stored: str | None) -> tuple[str, bool]:
    base = (stored or "").split(";", 1)[0].strip().lower()
    if base in _PASSED:
        return stored, False
    return "application/octet-stream", True
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_rules.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/results_rules.py packages/web/tests/test_results_rules.py
git commit -m "feat(web): which keys the results proxy serves, and as which content type"
```

---

### Task 3: The proxy app — config, S3 clients, login, logout, session

**Files:**
- Create: `packages/web/src/htrflow_web/results.py`
- Test: `packages/web/tests/test_results_login.py`

**Interfaces:**
- Consumes: Task 1 (`SessionCodec`, `derive_keys`, `load_key`, `COOKIE`), Task 2 (none yet).
- Produces:
  - `ResultsConfig` (pydantic, frozen) fields and env aliases: `namespace` (`HTRFLOW_RESULTS_NAMESPACE`, required), `s3_endpoint` (`S3_ENDPOINT`, ""), `s3_bucket` (`S3_BUCKET`, required), `s3_verify_tls` (`S3_VERIFY_TLS`, True), `session_key_file` (`HTRFLOW_SESSION_KEY_FILE`, "/secrets/session/key"), `session_hours` (`HTRFLOW_SESSION_HOURS`, 8.0), `key_derivation` (`HTRFLOW_KEY_DERIVATION`, "hcp", Literal["hcp","none"]); classmethod `from_env(env: Mapping[str,str] | None = None)`.
  - `ClientCache(cfg: ResultsConfig, maxsize: int = 256)` with `get(access_key: str, secret_key: str)` → boto3 S3 client.
  - `LoginLimiter(max_failures=5, window=60.0, clock=time.monotonic)` with `blocked(addr) -> bool`, `failed(addr) -> None`, `succeeded(addr) -> None`.
  - `create_results_app(cfg: ResultsConfig, codec: SessionCodec, clients: ClientCache | None = None, limiter: LoginLimiter | None = None) -> FastAPI`.
  - Routes: `POST /results/_login` (JSON `{"username","password"}`), `POST /results/_logout`, `GET /results/_session`.
  - `session_of(request, codec) -> SessionData | None` (module function, reused in Task 4).

- [ ] **Step 1: Write the failing tests**

```python
# packages/web/tests/test_results_login.py
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from htrflow_web.results import (
    ClientCache,
    LoginLimiter,
    ResultsConfig,
    create_results_app,
)
from htrflow_web.session import COOKIE, SessionCodec

KEY = bytes(range(32))
ORIGIN = {"Origin": "https://testserver"}


@pytest.fixture
def cfg():
    return ResultsConfig.from_env(
        {
            "HTRFLOW_RESULTS_NAMESPACE": "htr-test",
            "S3_BUCKET": "htr-results",
            "HTRFLOW_KEY_DERIVATION": "none",
        }
    )


@pytest.fixture
def app(cfg, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="htr-results")
        yield create_results_app(cfg, SessionCodec(KEY, hours=8))


def login(client, user="testing", password="testing", headers=ORIGIN):
    return client.post(
        "/results/_login", json={"username": user, "password": password}, headers=headers
    )


def test_a_valid_login_sets_the_session_cookie(app):
    c = TestClient(app, base_url="https://testserver")
    r = login(c)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    for attr in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attr.lower() in cookie.lower()
    assert c.get("/results/_session").json() == {"user": "testing"}


def test_plain_http_drops_only_secure(app):
    c = TestClient(app, base_url="http://testserver")
    r = login(c, headers={"Origin": "http://testserver"})
    assert r.status_code == 204
    assert "secure" not in r.headers["set-cookie"].lower()


def test_a_foreign_origin_is_refused_even_with_good_keys(app):
    c = TestClient(app, base_url="https://testserver")
    assert login(c, headers={"Origin": "https://evil.example"}).status_code == 403
    assert login(c, headers={}).status_code == 403


def test_wrong_keys_are_401_with_a_sentence(app, monkeypatch):
    c = TestClient(app, base_url="https://testserver")

    def refuse(self, access_key, secret_key):
        from botocore.exceptions import ClientError

        class Refusing:
            def head_object(self, **kw):
                raise ClientError(
                    {"Error": {"Code": "SignatureDoesNotMatch"}}, "HeadObject"
                )

        return Refusing()

    monkeypatch.setattr(ClientCache, "get", refuse)
    r = login(c)
    assert r.status_code == 401
    assert "user name or password" in r.json()["detail"]


def test_a_403_or_404_on_the_probe_means_the_keys_are_valid(app, monkeypatch):
    from botocore.exceptions import ClientError

    for code in ("403", "404", "AccessDenied", "NoSuchKey"):

        class Answering:
            def head_object(self, **kw):
                raise ClientError({"Error": {"Code": code}}, "HeadObject")

        monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Answering())
        c = TestClient(app, base_url="https://testserver")
        assert login(c).status_code == 204, code


def test_an_unreachable_store_is_502(app, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    class Down:
        def head_object(self, **kw):
            raise EndpointConnectionError(endpoint_url="https://store")

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Down())
    c = TestClient(app, base_url="https://testserver")
    assert login(c).status_code == 502


def test_five_failures_block_the_address(cfg):
    now = [0.0]
    limiter = LoginLimiter(clock=lambda: now[0])
    for _ in range(5):
        limiter.failed("1.2.3.4")
    assert limiter.blocked("1.2.3.4")
    assert not limiter.blocked("5.6.7.8")
    now[0] += 61
    assert not limiter.blocked("1.2.3.4")


def test_a_blocked_address_gets_429(cfg, monkeypatch):
    limiter = LoginLimiter()
    for _ in range(5):
        limiter.failed("testclient")
    app = create_results_app(cfg, SessionCodec(KEY, hours=8), limiter=limiter)
    c = TestClient(app, base_url="https://testserver")
    assert login(c).status_code == 429


def test_logout_clears_the_cookie(app):
    c = TestClient(app, base_url="https://testserver")
    login(c)
    r = c.post("/results/_logout", headers=ORIGIN)
    assert r.status_code == 204
    assert 'htr_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
    assert c.get("/results/_session").status_code == 401


def test_no_cookie_is_no_session(app):
    c = TestClient(app, base_url="https://testserver")
    assert c.get("/results/_session").status_code == 401


def test_the_client_cache_is_bounded(cfg):
    cache = ClientCache(cfg, maxsize=2)
    a, b, c3 = (cache.get(k, "s") for k in ("a", "b", "c"))
    assert len(cache) == 2
    assert cache.get("c", "s") is c3


def test_config_requires_namespace_and_bucket():
    with pytest.raises(ValueError):
        ResultsConfig.from_env({"S3_BUCKET": "b"})
    with pytest.raises(ValueError):
        ResultsConfig.from_env({"HTRFLOW_RESULTS_NAMESPACE": "n"})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_login.py`
Expected: FAIL, `htrflow_web.results` missing.

- [ ] **Step 3: Implement**

```python
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
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError
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


class ResultsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    namespace: str = Field(alias="HTRFLOW_RESULTS_NAMESPACE", min_length=1)
    s3_endpoint: str = Field("", alias="S3_ENDPOINT")
    s3_bucket: str = Field(alias="S3_BUCKET", min_length=1)
    s3_verify_tls: bool = Field(True, alias="S3_VERIFY_TLS")
    session_key_file: str = Field("/secrets/session/key", alias="HTRFLOW_SESSION_KEY_FILE")
    session_hours: float = Field(8.0, alias="HTRFLOW_SESSION_HOURS", gt=0)
    key_derivation: Literal["hcp", "none"] = Field("hcp", alias="HTRFLOW_KEY_DERIVATION")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ResultsConfig":
        env = os.environ if env is None else env
        names = {f.alias for f in cls.model_fields.values()}
        return cls(**{k: v for k, v in env.items() if k in names})


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

    def get(self, access_key: str, secret_key: str):
        k = (access_key, secret_key)
        with self._lock:
            if k in self._clients:
                self._clients.move_to_end(k)
                return self._clients[k]
        client = boto3.client(
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
        with self._lock:
            self._clients[k] = client
            while len(self._clients) > self._max:
                self._clients.popitem(last=False)
        return client


class LoginLimiter:
    def __init__(self, max_failures=5, window=60.0, clock=time.monotonic) -> None:
        self._max, self._window, self._clock = max_failures, window, clock
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, addr: str) -> deque[float]:
        q = self._fails.setdefault(addr, deque())
        cutoff = self._clock() - self._window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def blocked(self, addr: str) -> bool:
        with self._lock:
            return len(self._recent(addr)) >= self._max

    def failed(self, addr: str) -> None:
        with self._lock:
            self._recent(addr).append(self._clock())

    def succeeded(self, addr: str) -> None:
        with self._lock:
            self._fails.pop(addr, None)


def _code(e: ClientError) -> str:
    return str(e.response.get("Error", {}).get("Code", ""))


def _client_addr(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "")


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    if fwd_proto and fwd_host:
        expected = f"{fwd_proto}://{fwd_host}"
    return bool(origin) and origin == expected


def _secure(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


def session_of(request: Request, codec: SessionCodec) -> SessionData | None:
    token = request.cookies.get(COOKIE)
    return codec.open(token) if token else None


def _clear(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE, path="/", secure=_secure(request), httponly=True, samesite="strict")


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
    app.state.cfg, app.state.codec, app.state.clients = cfg, codec, clients

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.post("/results/_login")
    def login(body: Login, request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse({"detail": "cross-origin login refused"}, status_code=403)
        addr = _client_addr(request)
        if limiter.blocked(addr):
            return JSONResponse(
                {"detail": "too many failed logins: wait a minute and try again"},
                status_code=429,
            )
        ak, sk = derive_keys(body.username, body.password, cfg.key_derivation)
        try:
            clients.get(ak, sk).head_object(Bucket=cfg.s3_bucket, Key=f"{cfg.namespace}/")
        except ClientError as e:
            if _code(e) in BAD_KEYS:
                limiter.failed(addr)
                return JSONResponse(
                    {"detail": "the store did not accept that user name or password"},
                    status_code=401,
                )
            if _code(e) not in NOT_THERE | DENIED:
                _LOG.warning("login probe: store answered %s", _code(e))
                return JSONResponse({"detail": "the result store did not answer"}, status_code=502)
        except (ConnectTimeoutError, ReadTimeoutError):
            return JSONResponse({"detail": "the result store timed out"}, status_code=504)
        except BotoCoreError as e:
            _LOG.warning("login probe: %s", e)
            return JSONResponse({"detail": "the result store could not be reached"}, status_code=502)
        limiter.succeeded(addr)
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
            return JSONResponse({"detail": "cross-origin logout refused"}, status_code=403)
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
```

Note for the test `test_a_403_or_404_on_the_probe_means_the_keys_are_valid`: botocore reports a HEAD 404 as code `"404"` and a HEAD 403 as `"403"` (no body on HEAD), which is why both spellings are in `NOT_THERE`/`DENIED`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_login.py`
Expected: PASS. If moto rejects `head_object` on the key `htr-test/` with `404`, `test_a_valid_login_sets_the_session_cookie` still passes (404 = valid keys).

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/results.py packages/web/tests/test_results_login.py
git commit -m "feat(web): htrflow-results login, logout and session -- store keys checked once, sealed into the cookie"
```

---

### Task 4: Serving a file

**Files:**
- Modify: `packages/web/src/htrflow_web/results.py` (add the file route inside `create_results_app`)
- Test: `packages/web/tests/test_results_files.py`

**Interfaces:**
- Consumes: Task 2 `allowed_key`, `served_type`, `FILE_HEADERS`; Task 3 `session_of`, `_code`, `_clear`, `BAD_KEYS`, `NOT_THERE`, `DENIED`, `clients`.
- Produces: `GET`/`HEAD /results/{path:path}` on the proxy; other methods `405`.

- [ ] **Step 1: Write the failing tests**

```python
# packages/web/tests/test_results_files.py
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from htrflow_web.results import ClientCache, ResultsConfig, create_results_app
from htrflow_web.session import COOKIE, SessionCodec

KEY = bytes(range(32))


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="htr-results")
        s3.put_object(Bucket="htr-results", Key="htr-test/demo-v1/R1/iiif.json",
                      Body=b'{"id": 1}', ContentType="application/json")
        s3.put_object(Bucket="htr-results", Key="htr-test/demo-v1/R1/x.html",
                      Body=b"<script>1</script>", ContentType="text/html")
        s3.put_object(Bucket="htr-results", Key="status/logs/demo-v1/R1.txt",
                      Body=b"log line\n", ContentType="text/plain; charset=utf-8")
        cfg = ResultsConfig.from_env({"HTRFLOW_RESULTS_NAMESPACE": "htr-test",
                                      "S3_BUCKET": "htr-results",
                                      "HTRFLOW_KEY_DERIVATION": "none"})
        codec = SessionCodec(KEY, hours=8)
        c = TestClient(create_results_app(cfg, codec), base_url="https://testserver")
        c.cookies.set(COOKIE, codec.seal("testing", "testing", "testing"))
        yield c, codec


def test_a_result_file_streams_with_its_headers(setup):
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.status_code == 200
    assert r.content == b'{"id": 1}'
    assert r.headers["content-type"] == "application/json"
    assert r.headers["content-length"] == "9"
    assert r.headers["etag"]
    assert r.headers["last-modified"]
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_head_has_the_headers_and_no_body(setup):
    c, _ = setup
    r = c.head("/results/status/logs/demo-v1/R1.txt")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == "9"


def test_if_none_match_gives_304(setup):
    c, _ = setup
    etag = c.get("/results/htr-test/demo-v1/R1/iiif.json").headers["etag"]
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers["etag"] == etag
    assert r.content == b""


def test_html_is_never_rendered(setup):
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/x.html")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"


def test_missing_is_404_and_foreign_keys_never_reach_the_store(setup, monkeypatch):
    c, _ = setup
    assert c.get("/results/htr-test/demo-v1/R1/nope.json").status_code == 404
    monkeypatch.setattr(ClientCache, "get", lambda *a: pytest.fail("store asked"))
    assert c.get("/results/other-ns/x").status_code == 404
    assert c.get("/results/htr-test/a%2F..%2Fb").status_code == 404


def test_no_session_is_401(setup):
    c, _ = setup
    c.cookies.clear()
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 401


@pytest.mark.parametrize(
    "code,status",
    [("AccessDenied", 403), ("InvalidAccessKeyId", 401),
     ("SignatureDoesNotMatch", 401), ("InternalError", 502)],
)
def test_store_answers_map_to_statuses(setup, monkeypatch, code, status):
    from botocore.exceptions import ClientError

    class Answering:
        def get_object(self, **kw):
            raise ClientError({"Error": {"Code": code}}, "GetObject")

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Answering())
    c, _ = setup
    r = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert r.status_code == status
    if status == 401:
        assert COOKIE in r.headers["set-cookie"]  # cleared


def test_a_timeout_is_504(setup, monkeypatch):
    from botocore.exceptions import ReadTimeoutError

    class Slow:
        def get_object(self, **kw):
            raise ReadTimeoutError(endpoint_url="https://store")

    monkeypatch.setattr(ClientCache, "get", lambda self, a, s: Slow())
    c, _ = setup
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 504


def test_other_methods_are_405(setup):
    c, _ = setup
    assert c.put("/results/htr-test/demo-v1/R1/iiif.json", content=b"x").status_code == 405
    assert c.delete("/results/htr-test/demo-v1/R1/iiif.json").status_code == 405
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_files.py`
Expected: FAIL (404 from FastAPI for unknown route, or 405 assertions).

- [ ] **Step 3: Implement** — inside `create_results_app`, after the `_session` route (routes with a leading `_` segment are registered first, so they win):

```python
    @app.api_route("/results/{path:path}", methods=["GET", "HEAD"])
    def file(path: str, request: Request) -> Response:
        data = session_of(request, codec)
        if data is None:
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        raw = request.scope.get("raw_path", b"").decode("latin-1")
        raw = raw.split("/results/", 1)[1] if "/results/" in raw else path
        key = allowed_key(raw, cfg.namespace)
        if key is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        args = {"Bucket": cfg.s3_bucket, "Key": key}
        if inm := request.headers.get("if-none-match"):
            args["IfNoneMatch"] = inm
        if ims := request.headers.get("if-modified-since"):
            args["IfModifiedSince"] = ims
        s3 = clients.get(data.access_key, data.secret_key)
        try:
            obj = (s3.head_object if request.method == "HEAD" else s3.get_object)(**args)
        except ClientError as e:
            code = _code(e)
            if code in ("304", "NotModified"):
                etag = e.response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("etag", "")
                return Response(status_code=304, headers={"ETag": etag, **FILE_HEADERS})
            if code in NOT_THERE:
                return JSONResponse({"detail": "not found"}, status_code=404)
            if code in DENIED:
                return JSONResponse({"detail": "your account may not read this"}, status_code=403)
            if code in BAD_KEYS:
                r = JSONResponse({"detail": "your login is no longer accepted"}, status_code=401)
                _clear(r, request)
                return r
            _LOG.warning("store answered %s for %s", code, key)
            return JSONResponse({"detail": "the result store failed"}, status_code=502)
        except (ConnectTimeoutError, ReadTimeoutError):
            return JSONResponse({"detail": "the result store timed out"}, status_code=504)
        except BotoCoreError as e:
            _LOG.warning("store unreachable: %s", e)
            return JSONResponse({"detail": "the result store could not be reached"}, status_code=502)
        ctype, attachment = served_type(obj.get("ContentType"))
        headers = {**FILE_HEADERS, "Content-Length": str(obj["ContentLength"])}
        if etag := obj.get("ETag"):
            headers["ETag"] = etag
        if lm := obj.get("LastModified"):
            headers["Last-Modified"] = lm.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if attachment:
            headers["Content-Disposition"] = "attachment"
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type=ctype)
        body = obj["Body"]
        return StreamingResponse(
            body.iter_chunks(1024 * 1024), headers=headers, media_type=ctype,
            background=BackgroundTask(body.close),
        )
```

Add imports at the top of `results.py`:

```python
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .results_rules import FILE_HEADERS, allowed_key, served_type
```

If moto returns `LastModified` as a timezone-aware `datetime`, `strftime` above is correct (it is UTC). If `head_object` with `IfNoneMatch` returns 304 as code `"304"`, the branch handles it; if moto instead raises for `get_object` with code `"NotModified"`, also handled.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_files.py packages/web/tests/test_results_login.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/results.py packages/web/tests/test_results_files.py
git commit -m "feat(web): htrflow-results serves a result file with the user's keys -- 304, safe types, the store's answer mapped"
```

---

### Task 5: The `htrflow-results` entrypoint

**Files:**
- Modify: `packages/web/src/htrflow_web/__main__.py`, `packages/web/pyproject.toml` (`[project.scripts]`)
- Test: `packages/web/tests/test_results_main.py`

**Interfaces:**
- Consumes: Task 3 `ResultsConfig.from_env`, `create_results_app`; Task 1 `SessionCodec`, `load_key`.
- Produces: console script `htrflow-results` (uvicorn on `0.0.0.0:8082`); function `results_app_from_env(env=None) -> FastAPI`.

- [ ] **Step 1: Write the failing test**

```python
# packages/web/tests/test_results_main.py
import base64

import pytest

from htrflow_web.__main__ import results_app_from_env


def test_the_app_is_built_from_the_environment(tmp_path):
    key = tmp_path / "key"
    key.write_text(base64.b64encode(bytes(32)).decode())
    app = results_app_from_env({
        "HTRFLOW_RESULTS_NAMESPACE": "ns", "S3_BUCKET": "b",
        "HTRFLOW_SESSION_KEY_FILE": str(key),
    })
    assert app.state.cfg.namespace == "ns"


def test_a_missing_key_file_stops_startup(tmp_path):
    with pytest.raises(FileNotFoundError):
        results_app_from_env({
            "HTRFLOW_RESULTS_NAMESPACE": "ns", "S3_BUCKET": "b",
            "HTRFLOW_SESSION_KEY_FILE": str(tmp_path / "missing"),
        })
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest -q packages/web/tests/test_results_main.py`
Expected: FAIL, `ImportError: results_app_from_env`.

- [ ] **Step 3: Implement** — append to `__main__.py`:

```python
def results_app_from_env(env=None):
    from .results import ResultsConfig, create_results_app
    from .session import SessionCodec, load_key

    cfg = ResultsConfig.from_env(env)
    codec = SessionCodec(load_key(cfg.session_key_file), cfg.session_hours)
    if not cfg.s3_verify_tls:
        import logging

        logging.getLogger("htrflow_web.results").warning(
            "S3_VERIFY_TLS=false: the certificate of %s is not checked",
            cfg.s3_endpoint or "the default S3 endpoint",
        )
    return create_results_app(cfg, codec)


def results_main() -> None:
    uvicorn.run(results_app_from_env(), host="0.0.0.0", port=8082)
```

In `pyproject.toml` `[project.scripts]` add `htrflow-results = "htrflow_web.__main__:results_main"`. Update the module docstring's first line to name both entrypoints.

- [ ] **Step 4: Run to verify it passes**

Run: `uv sync --locked --all-packages && uv run --no-sync pytest -q packages/web/tests/test_results_main.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/__main__.py packages/web/pyproject.toml uv.lock packages/web/tests/test_results_main.py
git commit -m "feat(web): the htrflow-results entrypoint on :8082"
```

---

### Task 6: The web front behind the login

**Files:**
- Create: `packages/web/src/htrflow_web/sessions.py`
- Modify: `packages/web/src/htrflow_web/kube.py` (Config: add `results_proxy`, drop `s3_verify_tls`), `packages/web/src/htrflow_web/app.py` (create_app `sessions` param; gate `/api/v1/*`)
- Test: `packages/web/tests/test_sessions.py`, `packages/web/tests/test_kube.py`, `packages/web/tests/test_app.py`

**Interfaces:**
- Consumes: the proxy's `GET /results/_session` contract (Task 3).
- Produces:
  - `Session(user: str, cookie: str)` frozen dataclass.
  - `SessionChecker(proxy_base: str, client: httpx.Client | None = None, ttl: float = 30.0, maxsize: int = 1024, clock=time.monotonic)` with `check(cookie: str | None) -> Session | None`; raises `SessionsUnavailable` when the proxy does not answer.
  - `Config.results_proxy: str` (`HTRFLOW_RESULTS_PROXY`), required unless site-only; `from_env` raises `RuntimeError("HTRFLOW_RESULTS_PROXY is required")`.
  - `create_app(reader, static_dir=None, batch_version=DEV_VERSION, progress=None, sessions=None)`; a request-scoped dependency `current_session` returning `Session | None` (None only when no gate exists: a hand-built cfg without `results_proxy`).

- [ ] **Step 1: Write the failing tests**

```python
# packages/web/tests/test_sessions.py
import httpx
import pytest

from htrflow_web.sessions import Session, SessionChecker, SessionsUnavailable


def checker(handler, clock=lambda: 0.0):
    return SessionChecker("http://proxy/results",
                          httpx.Client(transport=httpx.MockTransport(handler)), clock=clock)


def test_a_valid_cookie_is_a_session_and_is_forwarded():
    seen = []

    def handler(req):
        seen.append(req.headers.get("cookie"))
        return httpx.Response(200, json={"user": "anna"})

    s = checker(handler).check("tok")
    assert s == Session("anna", "tok")
    assert seen == ["htr_session=tok"]


def test_no_cookie_never_asks():
    assert checker(lambda r: pytest.fail("asked")).check(None) is None


def test_401_is_no_session():
    assert checker(lambda r: httpx.Response(401)).check("tok") is None


def test_answers_are_cached_for_30_seconds():
    calls, now = [], [0.0]

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"user": "anna"})

    c = checker(handler, clock=lambda: now[0])
    c.check("tok"); c.check("tok")
    assert len(calls) == 1
    now[0] += 31
    c.check("tok")
    assert len(calls) == 2


def test_a_proxy_that_does_not_answer_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(SessionsUnavailable):
        checker(handler).check("tok")
```

In `packages/web/tests/test_kube.py` add:

```python
def test_the_results_proxy_is_required_outside_site_only():
    with pytest.raises(RuntimeError, match="HTRFLOW_RESULTS_PROXY"):
        Config.from_env({"HTRFLOW_RESULTS_URL": "https://x/results"})
    cfg = Config.from_env({"HTRFLOW_RESULTS_URL": "https://x/results",
                           "HTRFLOW_RESULTS_PROXY": "http://htrflow-results:8082/results"})
    assert cfg.results_proxy == "http://htrflow-results:8082/results"
    assert Config.from_env({"HTRFLOW_WEB_SITE_ONLY": "1"}).results_proxy == ""
```

Delete `test_the_bucket_certificate_is_verified_unless_the_secret_says_false` from `test_kube.py` (the web front no longer reads it). Update every existing `Config.from_env({...})` call in `test_kube.py` that sets `HTRFLOW_RESULTS_URL` without site-only to also set `"HTRFLOW_RESULTS_PROXY": "http://p/results"`.

In `packages/web/tests/test_app.py` add (near the `client` fixture):

```python
class FakeSessions:
    def __init__(self, users=None):
        self.users = users if users is not None else {"tok": "anna"}

    def check(self, cookie):
        from htrflow_web.sessions import Session

        user = self.users.get(cookie or "")
        return Session(user, cookie) if user else None


def test_the_api_is_401_without_a_session():
    c = TestClient(create_app(FakeReader(), progress=FakeProgress(), sessions=FakeSessions()))
    assert c.get("/api/v1/jobs").status_code == 401
    assert c.get("/api/v1/version").status_code == 401
    c.cookies.set("htr_session", "tok")
    assert c.get("/api/v1/jobs").status_code == 200


def test_the_site_itself_needs_no_session():
    c = TestClient(create_app(FakeReader(), progress=FakeProgress(), sessions=FakeSessions()))
    assert c.get("/healthz").status_code == 200
    assert c.get("/config.js").status_code == 200


def test_a_session_check_that_cannot_be_made_is_502():
    from htrflow_web.sessions import SessionsUnavailable

    class Down:
        def check(self, cookie):
            raise SessionsUnavailable("proxy down")

    c = TestClient(create_app(FakeReader(), progress=FakeProgress(), sessions=Down()))
    c.cookies.set("htr_session", "tok")
    assert c.get("/api/v1/jobs").status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_sessions.py packages/web/tests/test_kube.py packages/web/tests/test_app.py -k "session or proxy"`
Expected: FAIL.

- [ ] **Step 3: Implement `sessions.py`**

```python
# packages/web/src/htrflow_web/sessions.py
"""Whether a request is logged in -- asked of the results proxy, which alone
can open the cookie (docs: security, "Results"). Cached per cookie value."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx

COOKIE = "htr_session"


class SessionsUnavailable(Exception):
    """The results proxy did not answer the session check."""


@dataclass(frozen=True)
class Session:
    user: str
    cookie: str


class SessionChecker:
    def __init__(self, proxy_base: str, client: httpx.Client | None = None,
                 ttl: float = 30.0, maxsize: int = 1024, clock=time.monotonic) -> None:
        self._url = f"{proxy_base.rstrip('/')}/_session"
        self._client = client or httpx.Client(timeout=3.0)
        self._ttl, self._max, self._clock = ttl, maxsize, clock
        self._cache: OrderedDict[str, tuple[float, Session | None]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, cookie: str | None) -> Session | None:
        if not cookie:
            return None
        now = self._clock()
        with self._lock:
            hit = self._cache.get(cookie)
        if hit and hit[0] > now:
            return hit[1]
        try:
            r = self._client.get(self._url, headers={"Cookie": f"{COOKIE}={cookie}"})
        except httpx.HTTPError as e:
            raise SessionsUnavailable(str(e)) from e
        if r.status_code == 200:
            found: Session | None = Session(str(r.json()["user"]), cookie)
        elif r.status_code == 401:
            found = None
        else:
            raise SessionsUnavailable(f"session check answered {r.status_code}")
        with self._lock:
            self._cache[cookie] = (now + self._ttl, found)
            self._cache.move_to_end(cookie)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return found
```

- [ ] **Step 4: Implement the Config change** in `kube.py`: replace the `s3_verify_tls` field and its comment with

```python
    #: The results proxy's Service, e.g. http://htrflow-results:8082/results:
    #: the web front asks it whether a request is logged in, reads progress
    #: through it, and passes /results through to it. Required outside
    #: site-only mode.
    results_proxy: str = Field("", alias="HTRFLOW_RESULTS_PROXY")
```

In `from_env`, after the `HTRFLOW_RESULTS_URL is required` check:

```python
        proxy = (get("HTRFLOW_RESULTS_PROXY") or "").rstrip("/")
        if not proxy and not site_only:
            raise RuntimeError("HTRFLOW_RESULTS_PROXY is required")
```

and pass `HTRFLOW_RESULTS_PROXY=proxy` in the `cls(...)` call; remove `HTRFLOW_S3_VERIFY_TLS=...`.

- [ ] **Step 5: Implement the gate in `app.py`**

- Remove the `verify = getattr(reader.cfg, "s3_verify_tls", True)` block; build `progress = ProgressReader()` (Task 7 changes ProgressReader).
- Add the `sessions=None` parameter; after building `progress`:

```python
    if sessions is None and not site_only and getattr(reader.cfg, "results_proxy", ""):
        sessions = SessionChecker(reader.cfg.results_proxy)

    def current_session(request: Request) -> Session | None:
        """Every /api/v1 route depends on this. No checker means no gate:
        only a hand-built cfg (tests) lacks results_proxy -- Config.from_env
        refuses to start without one."""
        if sessions is None:
            return None
        try:
            found = sessions.check(request.cookies.get(COOKIE))
        except SessionsUnavailable as e:
            _LOG.warning("session check failed: %s", e)
            raise HTTPException(status_code=502, detail="the results service did not answer")
        if found is None:
            raise HTTPException(status_code=401, detail="not logged in")
        return found
```

- Add `session: Session | None = Depends(current_session)` as a parameter to `version`, `list_jobs` and `get_job` (and any other `/api/v1/` route in the file).
- Imports: `from fastapi import Depends, Request` (merge with the existing fastapi import), `from .sessions import COOKIE, Session, SessionChecker, SessionsUnavailable`.

- [ ] **Step 6: Run the web tests**

Run: `uv run --no-sync pytest -q packages/web/tests`
Expected: PASS (existing tests build apps with `FakeReader`, whose cfg has no `results_proxy`, so no gate).

- [ ] **Step 7: Commit**

```bash
git add packages/web/src/htrflow_web/sessions.py packages/web/src/htrflow_web/kube.py packages/web/src/htrflow_web/app.py packages/web/tests/test_sessions.py packages/web/tests/test_kube.py packages/web/tests/test_app.py
git commit -m "feat(web): /api/v1 answers only a logged-in request, as the results proxy says"
```

---

### Task 7: Progress through the proxy, per user

**Files:**
- Modify: `packages/web/src/htrflow_web/progress.py`, `packages/web/src/htrflow_web/app.py` (the two call sites that pass `progress.fetch`/`progress.cached`)
- Test: `packages/web/tests/test_progress.py`, `packages/web/tests/test_app.py` (`FakeProgress`)

**Interfaces:**
- Consumes: Task 6 `Session`.
- Produces:
  - `ProgressReader(client: httpx.Client | None = None)` (the `verify` parameter goes).
  - `ProgressReader.for_session(session: Session | None) -> SessionProgress` with the same `fetch(results_base, volume_id, state)` and `cached(results_base, volume_id, state)` signatures; requests carry `Cookie: htr_session=<cookie>`; cache key `(user, url)` (`user = ""` when `session is None`).
  - `FakeProgress.for_session(session)` returns `self` in tests.

- [ ] **Step 1: Write the failing tests** in `test_progress.py`:

```python
def test_the_session_cookie_goes_to_the_proxy():
    seen = []

    def handler(req):
        seen.append(req.headers.get("cookie"))
        return httpx.Response(200, json={"pagesDone": 1, "pagesTotal": 2})

    r = ProgressReader(httpx.Client(transport=httpx.MockTransport(handler)))
    from htrflow_web.sessions import Session

    r.for_session(Session("anna", "tok")).fetch("http://p/results/ns/demo", "R1", "running")
    assert seen and all(c == "htr_session=tok" for c in seen)


def test_one_users_answer_is_never_anothers():
    def handler(req):
        if req.headers.get("cookie") == "htr_session=a":
            return httpx.Response(200, json={"pagesDone": 1, "pagesTotal": 2})
        return httpx.Response(403)

    r = ProgressReader(httpx.Client(transport=httpx.MockTransport(handler)))
    from htrflow_web.sessions import Session

    a = r.for_session(Session("anna", "a"))
    b = r.for_session(Session("bo", "b"))
    assert a.fetch("http://p/results/ns/demo", "R1", "running") is not None
    assert b.cached("http://p/results/ns/demo", "R1", "running") == (False, None)
    assert b.fetch("http://p/results/ns/demo", "R1", "running") is None
```

Delete `test_the_reader_verifies_the_bucket_certificate_unless_told_not_to`. Change every existing `ProgressReader(...).fetch(...)`/`.cached(...)` in the file to go through `.for_session(None)`.

In `test_app.py`, add to `FakeProgress`:

```python
    def for_session(self, session):
        return self
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_progress.py`
Expected: FAIL, `AttributeError: for_session`.

- [ ] **Step 3: Implement**

In `progress.py`:
- `__init__(self, client=None)`: `self._client = client or httpx.Client(timeout=TIMEOUT)`; cache type `dict[tuple[str, str], tuple[float, dict | None, bool]]`.
- Rename the existing `fetch`/`cached`/`_read`/`_cached`/`_get`/`_body` to take two extra leading arguments `user: str, cookie: str | None`, use `(user, url)` as the cache key, and in `_body` send `headers={"Accept-Encoding": "identity", **({"Cookie": f"htr_session={cookie}"} if cookie else {})}`.
- Add:

```python
class SessionProgress:
    """The reader, bound to one caller's session: their cookie goes to the
    proxy and their answers stay in their own cache entries."""

    def __init__(self, reader: "ProgressReader", user: str, cookie: str | None):
        self._r, self._user, self._cookie = reader, user, cookie

    def fetch(self, results_base: str, volume_id: str, state: str) -> dict | None:
        return self._r._read(self._user, self._cookie, results_base, volume_id, state, True)[1]

    def cached(self, results_base, volume_id, state) -> tuple[bool, dict | None]:
        return self._r._read(self._user, self._cookie, results_base, volume_id, state, False)
```

and on `ProgressReader`:

```python
    def for_session(self, session) -> SessionProgress:
        if session is None:
            return SessionProgress(self, "", None)
        return SessionProgress(self, session.user, session.cookie)
```

Remove the public `ProgressReader.fetch`/`cached` (callers go through `for_session`).

In `app.py`, at both call sites (currently lines ~651 and ~689):

```python
        bound = progress.for_session(session) if progress is not None else None
        ...
            fetch_progress=bound.fetch if bound is not None else None,
            cached_progress=bound.cached if bound is not None else None,
```

(`session` is the `Depends(current_session)` parameter added in Task 6.)

- [ ] **Step 4: Run the web tests**

Run: `uv run --no-sync pytest -q packages/web/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/progress.py packages/web/src/htrflow_web/app.py packages/web/tests/test_progress.py packages/web/tests/test_app.py
git commit -m "feat(web): progress is read through the results proxy with the caller's session, cached per user"
```

---

### Task 8: `/results` passed through by the web front

**Files:**
- Create: `packages/web/src/htrflow_web/passthrough.py`
- Modify: `packages/web/src/htrflow_web/app.py` (register the route before the static mount)
- Test: `packages/web/tests/test_passthrough.py`

**Interfaces:**
- Consumes: `reader.cfg.results_proxy`.
- Produces: `results_route(app: FastAPI, proxy_base: str, client: httpx.AsyncClient | None = None) -> None` — registers `GET`, `HEAD`, `POST` on `/results/{path:path}`.
- Forwarded request headers: `cookie`, `origin`, `content-type`, `if-none-match`, `if-modified-since`, plus `x-forwarded-for` (client address appended), `x-forwarded-proto` and `x-forwarded-host` (from the incoming request, or preserved if already set by an Ingress).
- Returned headers: `content-type`, `content-length`, `etag`, `last-modified`, `cache-control`, `content-security-policy`, `x-content-type-options`, `content-disposition`, `set-cookie` (all values, repeated), `www-authenticate`.

- [ ] **Step 1: Write the failing tests**

```python
# packages/web/tests/test_passthrough.py
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from htrflow_web.passthrough import results_route


def app_with(handler):
    app = FastAPI()
    results_route(app, "http://proxy:8082/results",
                  httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(app, base_url="https://site.example")


def test_a_file_comes_through_with_its_headers():
    def handler(req):
        assert str(req.url) == "http://proxy:8082/results/ns/demo/R1/iiif.json"
        assert req.headers["cookie"] == "htr_session=tok"
        assert req.headers["x-forwarded-proto"] == "https"
        assert req.headers["x-forwarded-host"] == "site.example"
        return httpx.Response(200, content=b"{}", headers={
            "content-type": "application/json", "etag": '"e"',
            "content-security-policy": "default-src 'none'; sandbox"})

    c = app_with(handler)
    c.cookies.set("htr_session", "tok")
    r = c.get("/results/ns/demo/R1/iiif.json")
    assert r.status_code == 200 and r.content == b"{}"
    assert r.headers["etag"] == '"e"'
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_304_and_set_cookie_pass_unchanged():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(204, headers={"set-cookie": "htr_session=x; HttpOnly"})
        assert req.headers["if-none-match"] == '"e"'
        return httpx.Response(304, headers={"etag": '"e"'})

    c = app_with(handler)
    assert c.get("/results/ns/x", headers={"If-None-Match": '"e"'}).status_code == 304
    r = c.post("/results/_login", json={"username": "a", "password": "b"},
               headers={"Origin": "https://site.example"})
    assert r.status_code == 204
    assert r.headers["set-cookie"].startswith("htr_session=x")


def test_the_body_is_streamed_not_buffered():
    sent = b"x" * (3 * 1024 * 1024)
    c = app_with(lambda req: httpx.Response(200, content=sent,
                                            headers={"content-type": "text/plain"}))
    with c.stream("GET", "/results/ns/big.txt") as r:
        chunks = list(r.iter_bytes())
    assert b"".join(chunks) == sent


def test_a_proxy_that_does_not_answer_is_502():
    def handler(req):
        raise httpx.ConnectError("down")

    assert app_with(handler).get("/results/ns/x").status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/web/tests/test_passthrough.py`
Expected: FAIL, module missing.

- [ ] **Step 3: Implement**

```python
# packages/web/src/htrflow_web/passthrough.py
"""/results on the web front's origin, passed through to the results proxy:
one path in every install mode (docs: security, "Results"). This pod reads
nothing from the answer and cannot open the session cookie it forwards."""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

_LOG = logging.getLogger("htrflow_web.passthrough")

_UP = ("cookie", "origin", "content-type", "if-none-match", "if-modified-since")
_DOWN = ("content-type", "content-length", "etag", "last-modified", "cache-control",
         "content-security-policy", "x-content-type-options", "content-disposition",
         "www-authenticate")


def results_route(app: FastAPI, proxy_base: str,
                  client: httpx.AsyncClient | None = None) -> None:
    base = proxy_base.rstrip("/")
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0))

    @app.api_route("/results/{path:path}", methods=["GET", "HEAD", "POST"])
    async def results(path: str, request: Request):
        raw = request.scope.get("raw_path", b"").decode("latin-1")
        tail = raw.split("/results/", 1)[1] if "/results/" in raw else path
        headers = {k: v for k, v in request.headers.items() if k in _UP}
        peer = request.client.host if request.client else ""
        prior = request.headers.get("x-forwarded-for")
        headers["x-forwarded-for"] = f"{prior}, {peer}" if prior else peer
        headers["x-forwarded-proto"] = request.headers.get(
            "x-forwarded-proto", request.url.scheme)
        headers["x-forwarded-host"] = request.headers.get(
            "x-forwarded-host", request.headers.get("host", ""))
        body = await request.body() if request.method == "POST" else None
        try:
            upstream = await client.send(
                client.build_request(request.method, f"{base}/{tail}",
                                     headers=headers, content=body),
                stream=True,
            )
        except httpx.HTTPError as e:
            _LOG.warning("results proxy did not answer: %s", e)
            return JSONResponse({"detail": "the results service did not answer"},
                                status_code=502)
        out = {k: v for k, v in upstream.headers.items() if k in _DOWN}
        response = StreamingResponse(
            upstream.aiter_raw(), status_code=upstream.status_code, headers=out,
            background=BackgroundTask(upstream.aclose),
        )
        for cookie in upstream.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", cookie)
        return response
```

In `app.py`, before the static mount is added, when not site-only and `getattr(reader.cfg, "results_proxy", "")` is set:

```python
    if not site_only and getattr(reader.cfg, "results_proxy", ""):
        results_route(app, reader.cfg.results_proxy)
```

Import `from .passthrough import results_route`. The security-headers middleware uses `setdefault`, so the proxy's own CSP on file answers is kept.

- [ ] **Step 4: Run the web tests**

Run: `uv run --no-sync pytest -q packages/web/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/htrflow_web/passthrough.py packages/web/src/htrflow_web/app.py packages/web/tests/test_passthrough.py
git commit -m "feat(web): /results passed through to the results proxy, streamed, on the site's own origin"
```

---

### Task 9: Login page, 401 handling, log out

**Files:**
- Create: `frontend/src/lib/session.ts`, `frontend/src/routes/login/+page.svelte`, `frontend/src/routes/login/page.test.ts`, `frontend/src/lib/session.test.ts`
- Modify: `frontend/src/lib/api.ts` (`getResponse`), `frontend/src/routes/+layout.svelte` (log-out link)

**Interfaces:**
- Produces:
  - `class NotLoggedIn extends Error` (exported from `api.ts`), thrown by `getResponse` on a `401`.
  - `loginUrl(next: string): string` → `/login?next=<encoded>`; `safeNext(raw: string | null): string` → a same-site path starting with `/` and not `//`, else `/`.
  - `login(username, password): Promise<"ok" | "wrong" | "throttled" | "store">` (POST JSON to `/results/_login`, `credentials: "same-origin"`).
  - `logout(): Promise<void>` (POST `/results/_logout`).

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/src/lib/session.test.ts
import { describe, expect, test, vi } from "vitest";
import { login, loginUrl, safeNext } from "./session";

describe("session helpers", () => {
  test("loginUrl keeps where the user was", () => {
    expect(loginUrl("/log?log=a&manifest=b")).toBe(
      "/login?next=%2Flog%3Flog%3Da%26manifest%3Db",
    );
  });
  test("safeNext refuses other sites", () => {
    expect(safeNext("/alto?src=x")).toBe("/alto?src=x");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });
  test("login maps the proxy's answers", async () => {
    for (const [status, want] of [
      [204, "ok"], [401, "wrong"], [429, "throttled"], [502, "store"],
    ] as const) {
      globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status }));
      expect(await login("a", "b")).toBe(want);
    }
  });
});
```

```ts
// frontend/src/routes/login/page.test.ts
import { render, screen, fireEvent } from "@testing-library/svelte";
import { expect, test, vi } from "vitest";
import Page from "./+page.svelte";

test("a wrong password says so and stays on the page", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
  render(Page);
  await fireEvent.input(screen.getByLabelText(/user/i), { target: { value: "a" } });
  await fireEvent.input(screen.getByLabelText(/password/i), { target: { value: "b" } });
  await fireEvent.click(screen.getByRole("button", { name: /log in/i }));
  expect(await screen.findByText(/did not accept/i)).toBeTruthy();
});
```

Add to the existing `frontend/src/routes/page.test.ts` a test that a `401` from `/api/v1/jobs` sets `window.location` to `/login?next=%2F` (mock `fetch` → `new Response(null, {status: 401})`, stub `location.assign` with `vi.fn()` and assert it was called with that URL).

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && bun run test -- session login page`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `session.ts`**

```ts
// frontend/src/lib/session.ts
/** The results proxy's login, behind which the whole site sits. */

export function loginUrl(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}

export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export async function login(
  username: string,
  password: string,
): Promise<"ok" | "wrong" | "throttled" | "store"> {
  const res = await fetch("/results/_login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 204) return "ok";
  if (res.status === 401) return "wrong";
  if (res.status === 429) return "throttled";
  return "store";
}

export async function logout(): Promise<void> {
  await fetch("/results/_logout", { method: "POST", credentials: "same-origin" });
}
```

- [ ] **Step 4: Implement the 401 handling** in `api.ts`: add

```ts
export class NotLoggedIn extends Error {}
```

and in `getResponse`, before `if (!res.ok) …`:

```ts
  if (res.status === 401) {
    const here = location.pathname + location.search + location.hash;
    location.assign(loginUrl(here));
    throw new NotLoggedIn("not logged in");
  }
```

(import `loginUrl` from `./session`). Callers that catch `ApiUnreachable` must not show "unreachable" for `NotLoggedIn`: in each `catch` in `+page.svelte`, `log/+page.svelte` and `alto/+page.svelte` that sets an unreachable message, return early when `e instanceof NotLoggedIn`. The log and alto routes' direct `fetch`es of result files: on `401`, call the same `location.assign(loginUrl(...))`.

- [ ] **Step 5: Implement the login page**

```svelte
<!-- frontend/src/routes/login/+page.svelte -->
<script lang="ts">
  import { login, safeNext } from "$lib/session";

  let username = $state("");
  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  const messages = {
    wrong: "The result store did not accept that user name or password.",
    throttled: "Too many failed attempts. Wait a minute and try again.",
    store: "The result store could not be reached. Try again shortly.",
  } as const;

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    busy = true;
    error = "";
    const outcome = await login(username, password);
    busy = false;
    password = "";
    if (outcome === "ok") {
      location.assign(safeNext(new URLSearchParams(location.search).get("next")));
    } else {
      error = messages[outcome];
    }
  }
</script>

<main class="login">
  <h1>Log in</h1>
  <p>Use your result-store account.</p>
  <form onsubmit={submit}>
    <label>User name <input bind:value={username} autocomplete="username" required /></label>
    <label>Password
      <input type="password" bind:value={password} autocomplete="current-password" required />
    </label>
    <button type="submit" disabled={busy}>Log in</button>
  </form>
  {#if error}<p role="alert">{error}</p>{/if}
</main>
```

In `+layout.svelte`, next to `ThemeToggle`, add a "Log out" button that calls `logout()` then `location.assign("/login")`; hide it on `/login` (use `$page.url.pathname` from `$app/stores` or the project's existing routing idiom).

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && bun run test && bun run check && bun run lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): a login page; any 401 goes there and back; log out"
```

---

### Task 10: Chart — the proxy, and the web front without S3

**Files:**
- Create: `charts/htrflow-batch/templates/results.yaml`
- Modify: `charts/htrflow-batch/templates/web.yaml`, `charts/htrflow-batch/values.yaml`, `charts/htrflow-batch/values.schema.json`, `charts/htrflow-batch/templates/_helpers.tpl` (refuse `web.internalResultsBase`), `charts/htrflow-batch/values-prod.yaml`, `charts/htrflow-batch/ci/default-values.yaml`, `charts/htrflow-batch/ci/full-values.yaml`, `charts/htrflow-batch/Chart.yaml` (0.16.0)
- Test: `packages/converter/tests/test_chart_render.py`

**Interfaces:**
- Produces values: `results.replicas` (1), `results.resources` (requests cpu 50m / memory 128Mi; limits cpu 500m / memory 256Mi), `results.sessionSecret` ("" → required), `results.sessionHours` (8), `results.keyDerivation` ("hcp").
- The web Deployment env: `HTRFLOW_RESULTS_PROXY=http://htrflow-results:8082/results`, `HTRFLOW_INTERNAL_RESULTS_BASE=http://htrflow-results:8082/results`; no `HTRFLOW_S3_VERIFY_TLS`; no reference to `s3.existingSecret`.

- [ ] **Step 1: Write the failing chart tests** in `test_chart_render.py` (reuse its `render`, `DEFAULT_SETS`, guard-table helpers):

```python
SESSION = ("results.sessionSecret=htr-session",)


def _by(objs, kind, name):
    return next(o for o in objs if o["kind"] == kind and o["metadata"]["name"] == name)


def test_the_results_proxy_holds_no_token_and_no_bucket_credential():
    objs = render(sets=DEFAULT_SETS + SESSION)
    dep = _by(objs, "Deployment", "htrflow-results")
    spec = dep["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert [v["secret"]["secretName"] for v in spec["volumes"] if "secret" in v] == ["htr-session"]
    env = {e["name"]: e for e in spec["containers"][0]["env"]}
    for key in ("S3_ENDPOINT", "S3_BUCKET", "S3_VERIFY_TLS"):
        assert env[key]["valueFrom"]["secretKeyRef"]["key"] == key
    assert "credentials" not in json.dumps(spec)
    assert env["HTRFLOW_RESULTS_NAMESPACE"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.namespace"
    assert spec["containers"][0]["command"] == ["/app/.venv/bin/htrflow-results"]
    assert not any(o["kind"] in ("Role", "RoleBinding") and "results" in o["metadata"]["name"] for o in objs)


def test_the_web_front_no_longer_touches_the_s3_secret():
    objs = render(sets=DEFAULT_SETS + SESSION)
    web = _by(objs, "Deployment", "htrflow-web")
    text = json.dumps(web)
    assert "htr-batch-s3" not in text and "S3_VERIFY_TLS" not in text
    env = {e["name"]: e.get("value") for e in web["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["HTRFLOW_RESULTS_PROXY"] == "http://htrflow-results:8082/results"
    assert env["HTRFLOW_INTERNAL_RESULTS_BASE"] == "http://htrflow-results:8082/results"


def test_the_network_policies_route_web_to_proxy_and_proxy_to_s3():
    objs = render(sets=DEFAULT_SETS + SESSION + ("network.enabled=true",))
    web = _by(objs, "NetworkPolicy", "htr-web")
    res = _by(objs, "NetworkPolicy", "htr-results")
    assert {"podSelector": {"matchLabels": {"app": "htrflow-results"}}} in [
        t for rule in web["spec"]["egress"] for t in rule.get("to", [])]
    assert res["spec"]["ingress"][0]["from"] == [{"podSelector": {"matchLabels": {"app": "htrflow-web"}}}]
    assert res["spec"]["ingress"][0]["ports"] == [{"port": 8082}]
```

Add guard-table cases (the existing `test_each_guard_refuses_in_its_own_words` table):

```python
    "session-secret": (
        None,
        DEFAULT_SETS,
        "results.sessionSecret is required: the name of a Secret with key `key` "
        "(32 random bytes, base64) that seals login sessions",
    ),
    "internal-results-base-gone": (
        None,
        DEFAULT_SETS + SESSION + ("web.internalResultsBase=http://x",),
        "web.internalResultsBase is gone (chart 0.16.0): the web front reads "
        "the bucket through the results proxy; remove the key",
    ),
```

`DEFAULT_SETS` elsewhere in the file must gain `results.sessionSecret=htr-session` for every render that expects success; add it to the module-level `DEFAULT_SETS` tuple, and to `ci/default-values.yaml` / `ci/full-values.yaml` as `results: {sessionSecret: htr-session}`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest -q packages/converter/tests/test_chart_render.py -k "results or web_front or network_policies_route or guard"`
Expected: FAIL.

- [ ] **Step 3: Implement `templates/results.yaml`**

```yaml
{{- /* charts/htrflow-batch/templates/results.yaml
The results proxy: result files on the web front's origin, read from the
bucket with each logged-in user's own store keys (docs: security,
"Results"). No credentials of its own, no ServiceAccount token. */}}
{{- $sessionSecret := .Values.results.sessionSecret | required "results.sessionSecret is required: the name of a Secret with key `key` (32 random bytes, base64) that seals login sessions" }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: htrflow-results
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "htrflow-batch.labels" . | nindent 4 }}
    app: htrflow-results
spec:
  replicas: {{ .Values.results.replicas }}
  selector:
    matchLabels: { app: htrflow-results }
  template:
    metadata:
      labels: { app: htrflow-results }
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: results
          image: {{ include "htrflow-batch.pinnedImage" (list .Values.web.image "web.image" .) }}
          command: ["/app/.venv/bin/htrflow-results"]
          ports:
            - { name: http, containerPort: 8082 }
          env:
            - { name: HOME, value: /tmp }
            - name: HTRFLOW_RESULTS_NAMESPACE
              valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
            - { name: HTRFLOW_SESSION_KEY_FILE, value: /secrets/session/key }
            - { name: HTRFLOW_SESSION_HOURS, value: {{ .Values.results.sessionHours | quote }} }
            - { name: HTRFLOW_KEY_DERIVATION, value: {{ .Values.results.keyDerivation | quote }} }
            {{- range list "S3_ENDPOINT" "S3_BUCKET" "S3_VERIFY_TLS" }}
            - name: {{ . }}
              valueFrom:
                secretKeyRef:
                  name: {{ $.Values.s3.existingSecret }}
                  key: {{ . }}
                  {{- if ne . "S3_BUCKET" }}
                  optional: true
                  {{- end }}
            {{- end }}
          readinessProbe:
            httpGet: { path: /healthz, port: http }
          livenessProbe:
            httpGet: { path: /healthz, port: http }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          resources: {{- toYaml .Values.results.resources | nindent 12 }}
          volumeMounts:
            - { name: session, mountPath: /secrets/session, readOnly: true }
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: session
          secret: { secretName: {{ $sessionSecret }}, defaultMode: 0440 }
        - name: tmp
          emptyDir: { sizeLimit: 64Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: htrflow-results
  namespace: {{ .Release.Namespace }}
  labels: {{- include "htrflow-batch.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  selector: { app: htrflow-results }
  ports:
    - { name: http, port: 8082, targetPort: http }
{{- if .Values.network.enabled }}
---
{{- $s3 := include "htrflow-batch.s3Egress" . | fromJsonArray }}
# Only the web front talks to the proxy (it passes /results through); the
# proxy talks only to DNS and the store.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: htr-results
  namespace: {{ .Release.Namespace }}
  labels: {{- include "htrflow-batch.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels: { app: htrflow-results }
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - podSelector: { matchLabels: { app: htrflow-web } }
      ports:
        - { port: 8082 }
  egress:
    - to:
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } }
          podSelector: { matchLabels: { k8s-app: kube-dns } }
      ports:
        - { port: 53, protocol: UDP }
        - { port: 53, protocol: TCP }
    {{- range $s3 }}
    - {{ toJson . }}
    {{- end }}
{{- end }}
```

Check `_helpers.tpl` for the pinned-image helper's real name and argument shape (the web Deployment's `image:` line shows how it is called) and use exactly that call.

- [ ] **Step 4: Change `web.yaml`**

- Replace the `HTRFLOW_INTERNAL_RESULTS_BASE` entry and the `HTRFLOW_S3_VERIFY_TLS` block with:

```yaml
            # The results proxy (templates/results.yaml): the web front asks it
            # whether a request is logged in, reads progress through it, and
            # passes /results through to it. It holds no bucket access itself.
            - name: HTRFLOW_RESULTS_PROXY
              value: http://htrflow-results:8082/results
            - name: HTRFLOW_INTERNAL_RESULTS_BASE
              value: http://htrflow-results:8082/results
```

- In the `htr-web` NetworkPolicy: replace the `$s3` egress entries with

```yaml
    - to:
        - podSelector: { matchLabels: { app: htrflow-results } }
      ports:
        - { port: 8082 }
```

  and update the comment above the policy (no S3 egress: progress goes through the proxy). Remove the `{{- $s3 := … }}` line from web.yaml.

- [ ] **Step 5: Values, schema, guard**

`values.yaml` — remove `web.internalResultsBase` and its comment; add after `web:`:

```yaml
# The results proxy (templates/results.yaml): result files on the web
# front's origin, read with each logged-in user's own store keys.
results:
  replicas: 1
  # A Secret with key `key`: 32 random bytes, base64. It seals login
  # sessions; rotating it logs everyone out. Required.
  #   kubectl create secret generic htr-session --from-literal=key="$(openssl rand -base64 32)"
  sessionSecret: ""
  sessionHours: 8
  # hcp: the login takes the store account's user name and password and
  # derives its S3 keys (HCP). none: the login takes the S3 access key and
  # secret key as they are (RustFS, MinIO, AWS).
  keyDerivation: hcp
  resources:
    requests: { cpu: 50m, memory: 128Mi }
    limits: { cpu: 500m, memory: 256Mi }
```

`values.schema.json` — add `"results"` to `required` and a `results` object (`additionalProperties: false`; `replicas` integer ≥1; `sessionSecret` string with the Secret-name pattern used by `s3.existingSecret` or empty; `sessionHours` number > 0; `keyDerivation` enum `["hcp","none"]`; `resources` object). Remove `internalResultsBase` from the `web` properties, and add a top-level-of-`web` property `"internalResultsBase": {"description": "Removed in 0.16.0; admitted only so htrflow-batch.validate can refuse it by name."}` the same way `publicResultsBase` is admitted.

`_helpers.tpl`, inside `htrflow-batch.validate`, next to the `publicResultsBase` refusal:

```
{{- if hasKey .Values.web "internalResultsBase" }}
{{- fail "web.internalResultsBase is gone (chart 0.16.0): the web front reads the bucket through the results proxy; remove the key" }}
{{- end }}
```

`values-prod.yaml` — add `results: {sessionSecret: ""}` to its "must be set" list with the same comment style it uses for the other required values. `Chart.yaml` version `0.16.0`.

- [ ] **Step 6: Run the chart tests and the Kyverno checks**

Run: `uv run --no-sync pytest -q packages/converter/tests/test_chart_render.py packages/converter/tests/test_chart_agreement.py packages/converter/tests/test_policy_admission.py`
Expected: PASS. If `test_chart_agreement.py` fails on the generated configuration page, run `make config-reference` (Task 12 regenerates it again; it is fine to regenerate here).

- [ ] **Step 7: Commit**

```bash
git add charts/htrflow-batch packages/converter/tests/test_chart_render.py
git commit -m "feat(chart): the results proxy Deployment; the web front loses its S3 access; chart 0.16.0"
```

---

### Task 11: Dev stacks — a private bucket and a login user

**Files:**
- Modify: `charts/htrflow-devstack/templates/rustfs.yaml`, `charts/htrflow-devstack/templates/_helpers.tpl`, `charts/htrflow-devstack/values.yaml`, `charts/htrflow-devstack/values.schema.json`, `charts/htrflow-devstack/Chart.yaml` (0.5.0), `scripts/compose_init.py`, `.docker/docker-compose.yml`
- Test: `packages/converter/tests/test_chart_render.py` (devstack section), `packages/wrapper/tests/test_compose_stack.py`

**Interfaces:**
- Produces devstack values: `s3.loginUser` (default `htr-reader`), `s3.loginSecret` (a Secret with key `password`; the chart creates it with a random password when `devStack.insecureDefaults` is false and the Secret does not exist, using `lookup` so it survives upgrades).
- The login user has a read-only policy on `s3.bucket` (`s3:GetObject`, `s3:HeadObject` via GetObject, `s3:ListBucket` not granted).

- [ ] **Step 1: Verify how RustFS creates a user with a policy.** Run locally:

```bash
docker run -d --name rfs -p 19900:9000 -e RUSTFS_ACCESS_KEY=admin -e RUSTFS_SECRET_KEY=adminadmin rustfs/rustfs:latest
docker run --rm --network host --entrypoint sh minio/mc -c '
  mc alias set r http://127.0.0.1:19900 admin adminadmin &&
  mc mb -p r/htr-results &&
  printf "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\"],\"Resource\":[\"arn:aws:s3:::htr-results/*\"]}]}" > /tmp/p.json &&
  mc admin policy create r htr-read /tmp/p.json &&
  mc admin user add r htr-reader readerpass1 &&
  mc admin policy attach r htr-read --user htr-reader && echo OK'
docker rm -f rfs
```

Expected: `OK`. **If any `mc admin` command fails, stop and report to the controller** with the exact error: the dev-stack login user then needs a different mechanism, which is a design decision, not something to improvise. Use the RustFS image tag the devstack chart already pins (`values.yaml` `rustfs.image`), not `latest`, for the real check.

- [ ] **Step 2: Write the failing tests** (devstack render): the `rustfs-init` ConfigMap has no `results-policy.json` and no `cors.json`; its `init.sh` creates policy `htr-read` and user `s3.loginUser` and attaches the policy; `values.yaml` has no `publicLogs`/`corsOrigins`. In `test_compose_stack.py`: `compose_init.py`'s results bucket gets no bucket policy and no CORS; the fixtures bucket keeps its anonymous-read policy; a login user is created.

- [ ] **Step 3: Implement** — `rustfs.yaml` `init.sh`: drop the `put-bucket-policy`/`put-bucket-cors` lines for `$S3_BUCKET` and the two ConfigMap data keys; add the `mc` commands from Step 1 against `$S3_ENDPOINT` with the admin keys already in the init Job's env and the login password from `s3.loginSecret`. The init container image must provide `mc`: switch `rustfs.init.image` to a pinned `minio/mc` image if the AWS CLI image cannot do admin calls (it cannot), and keep `aws` only if still needed for `create-bucket` (otherwise use `mc mb -p`). Remove `bucketPolicy` from `_helpers.tpl`, `publicLogs` and `corsOrigins` from values and schema. `compose_init.py`: drop `anonymous_read_policy` for the results bucket and the results bucket's CORS; keep the fixtures bucket's policy and CORS (it plays the external IIIF server); create the login user with the same admin calls through a `mc` service in `docker-compose.yml`. In `docker-compose.yml`: the `web` service gains `HTRFLOW_RESULTS_PROXY: http://results:8082/results`; add a `results` service on the same web image with `command: ["/app/.venv/bin/htrflow-results"]`, `HTRFLOW_KEY_DERIVATION: none`, the session key from a generated file, `S3_ENDPOINT: http://rustfs:<port>`, `S3_BUCKET`; `RESULTS_URL` for the wrapper becomes `http://localhost:<web port>/results`.

- [ ] **Step 4: Run the tests**

Run: `uv run --no-sync pytest -q packages/converter/tests/test_chart_render.py packages/wrapper/tests/test_compose_stack.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add charts/htrflow-devstack scripts/compose_init.py .docker/docker-compose.yml packages/converter/tests/test_chart_render.py packages/wrapper/tests/test_compose_stack.py
git commit -m "feat(devstack): the results bucket is private; RustFS gets a read-only login user; devstack 0.5.0"
```

---

### Task 12: Docs, generated reference, budgets

**Files:**
- Modify: `docs/getting-started/deploy.md`, `docs/how-it-works/security.md`, `docs/getting-started/viewing.md`, `docs/how-it-works/decision-log.md`, `docs/reference/chart.md`, `docs/reference/web.md`, `docs/reference/s3-layout.md`, `scripts/config_reference.md`, `scripts/config_reference.py`, `docs/reference/configuration.md` (generated), `charts/htrflow-batch/README.md`, `charts/htrflow-devstack/README.md`, `packages/web/README.md`, `scripts/loc-budget.sh`, `docs/features/batch-kueue-helm/stories/B67-read-api-auth-and-public-logs.md`

- [ ] **Step 1: Site docs** (docs-lint rules apply: no names, dates, versions, hosts, story ids):
  - deploy.md: remove the anonymous-read table and the CORS rule; add "The results bucket stays private" with: store accounts need read permission; create the session Secret (`kubectl create secret generic htr-session --from-literal=key="$(openssl rand -base64 32)"`) and set `results.sessionSecret`; `resultsUrl` is `https://<web front host>/results`; `results.keyDerivation` for stores that issue keys; the overwrite requirement (versioning with pruning where the store needs it).
  - security.md: replace "The read API holds no S3 credential… same public-read policy a browser relies on" with the new boundary: no pod holds a credential to read results; the proxy reads with the user's keys sealed in an `HttpOnly` cookie it alone can open; the web front cannot open it; the content-type rule and the sandbox CSP. Update the anonymous-read table (nothing is anonymous any more).
  - viewing.md: logging in; volumes published before `resultsUrl` pointed at `/results` must be run again to open in the viewer; fix the sentence that says page images come from the bucket (they come from the IIIF source).
  - decision-log.md: a new row: logins use the result store's own accounts; the proxy reads with each user's keys; everything is behind the login; this replaces the earlier plan of logging in through GitHub and Hugging Face org membership.
  - chart.md, web.md, s3-layout.md: the `results.*` values; `HTRFLOW_RESULTS_PROXY`; `/results` routes; "public-read" wording removed.
- [ ] **Step 2: Generated reference** — in `scripts/config_reference.py` add entries so the new env vars get correct sources (`HTRFLOW_RESULTS_PROXY`: "the chart, fixed: the results proxy's Service"; the proxy's own env: `HTRFLOW_RESULTS_NAMESPACE`, `HTRFLOW_SESSION_KEY_FILE`, `HTRFLOW_SESSION_HOURS`, `HTRFLOW_KEY_DERIVATION`) and remove `HTRFLOW_S3_VERIFY_TLS`; add a surface for the proxy's `ResultsConfig` model beside the web one if the generator has a surface list (`SURFACES`). Run `make config-reference`; commit the regenerated page.
- [ ] **Step 3: Chart READMEs** — `htrflow-batch` README: "From 0.15.0 to 0.16.0" upgrade table (set `results.sessionSecret`; set `resultsUrl` to `…/results`; `web.internalResultsBase` refused; the bucket no longer needs anonymous read or CORS — remove them after upgrading; store accounts need read permission) and a `0.16.0 — unreleased` changelog entry. `htrflow-devstack` README: `0.5.0 — unreleased` (private bucket, login user, `publicLogs`/`corsOrigins` gone).
- [ ] **Step 4: Story file** — in `B67-…md` add under "Vad som levereras" a line that the login uses the store's own accounts (decided), replacing Dex/oauth2-proxy for now; leave the rest as the story's history.
- [ ] **Step 5: Budgets** — run `scripts/loc-budget.sh`; for each package over budget raise its number with a comment in the file's existing style naming what grew and why (e.g. "results proxy: session.py, results.py, results_rules.py, sessions.py, passthrough.py").
- [ ] **Step 6: Gates**

Run: `make ci && scripts/loc-budget.sh && ZENSICAL=<a zensical binary> scripts/docs-site.sh build --clean --strict`
Expected: all pass.

- [ ] **Step 7: Commit** (one commit per bullet group: site docs; generated reference; chart READMEs; story; budgets).

---

### Task 13: Local end-to-end check and the PR

- [ ] **Step 1: Proxy against HTTPS S3 with an untrusted certificate.** As in the earlier `S3_VERIFY_TLS` check: a self-signed certificate, `moto.server` over HTTPS (`uv run --no-sync --with 'moto[server]==5.1.*' python -m moto.server -H 127.0.0.1 -p 4443 -c cert.pem -k key.pem`), a bucket with one `htr-test/demo/R1/iiif.json`. Start `htrflow-results` with `S3_ENDPOINT=https://127.0.0.1:4443`, `S3_VERIFY_TLS=false`, `HTRFLOW_KEY_DERIVATION=none`, a key file, namespace `htr-test`, and in front of it `htrflow-web`'s pass-through (a small script that builds `FastAPI()` + `results_route(app, "http://127.0.0.1:8082/results")` on :8081). With `curl -c jar -b jar`:
  - `GET /results/htr-test/demo/R1/iiif.json` → 401;
  - `POST /results/_login` with `Origin: http://127.0.0.1:8081` and moto's keys → 204 and a cookie;
  - the same GET → 200 with the file; again with `If-None-Match` → 304;
  - `GET /results/other/x` → 404; `POST /results/_logout` → 204; the GET → 401.
  Record the transcript in the PR description.
- [ ] **Step 2: Push and open the PR** — `git push -u origin spec/results-login`; `gh pr create` titled "feat: results behind a login — a proxy that reads the bucket with each user's own store keys", body summarising the spec sections, the upgrade steps, the gates and the end-to-end transcript.
