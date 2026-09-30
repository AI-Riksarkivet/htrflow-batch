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
        "/results/_login",
        json={"username": user, "password": password},
        headers=headers,
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
    assert (
        'htr_session=""' in r.headers["set-cookie"]
        or "Max-Age=0" in r.headers["set-cookie"]
    )
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
