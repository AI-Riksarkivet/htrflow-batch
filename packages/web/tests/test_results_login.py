# packages/web/tests/test_results_login.py
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from htrflow_web.cookie import COOKIE
from htrflow_web.results import (
    ClientCache,
    LoginLimiter,
    ResultsConfig,
    create_results_app,
)
from htrflow_web.session import SessionCodec

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
    assert cookie.startswith(f"__Host-{COOKIE}=")
    for attr in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attr.lower() in cookie.lower()
    assert c.get("/results/_session").json() == {"user": "testing"}


def test_plain_http_drops_secure_and_the_prefix(app):
    c = TestClient(app, base_url="http://testserver")
    r = login(c, headers={"Origin": "http://testserver"})
    assert r.status_code == 204
    assert "secure" not in r.headers["set-cookie"].lower()
    assert r.headers["set-cookie"].startswith(f"{COOKIE}=")
    assert c.get("/results/_session").json() == {"user": "testing"}


def test_over_https_a_cookie_without_the_prefix_is_no_session(app):
    """A sibling subdomain, or a plain-HTTP answer on the same host, can set
    `htr_session` but never `__Host-htr_session`: a planted session of the
    attacker's own account must not be taken."""
    planted = TestClient(app, base_url="http://testserver")
    login(planted, headers={"Origin": "http://testserver"})
    token = planted.cookies[COOKIE]
    c = TestClient(app, base_url="https://testserver")
    c.cookies.set(COOKIE, token)
    assert c.get("/results/_session").status_code == 401
    c.cookies.set(f"__Host-{COOKIE}", token)
    assert c.get("/results/_session").json() == {"user": "testing"}


def test_a_foreign_origin_is_refused_even_with_good_keys(app):
    c = TestClient(app, base_url="https://testserver")
    assert login(c, headers={"Origin": "https://evil.example"}).status_code == 403
    assert login(c, headers={}).status_code == 403


#: What the web front adds to every request it passes through: the proxy
#: itself is reached at a pod address over plain HTTP.
EDGE = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "site.example"}


def test_a_forwarded_login_is_judged_against_the_browsers_origin(app):
    c = TestClient(app, base_url="http://htrflow-results:8082")
    r = login(c, headers={**EDGE, "Origin": "https://site.example"})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"__Host-{COOKIE}=") and "secure" in cookie.lower()


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://site.example",  # the scheme is part of the origin
        "http://htrflow-results:8082",  # the pod's own origin is not the site's
    ],
)
def test_a_forwarded_login_from_any_other_origin_is_refused(app, origin):
    c = TestClient(app, base_url="http://htrflow-results:8082")
    r = login(c, headers={**EDGE, "Origin": origin})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


def test_a_forwarded_plain_http_login_is_not_secure(app):
    c = TestClient(app, base_url="https://htrflow-results:8082")
    edge = {"X-Forwarded-Proto": "http", "X-Forwarded-Host": "site.example:30800"}
    r = login(c, headers={**edge, "Origin": "http://site.example:30800"})
    assert r.status_code == 204
    assert r.headers["set-cookie"].startswith(f"{COOKIE}=")
    assert "secure" not in r.headers["set-cookie"].lower()


def test_wrong_keys_are_401_with_a_sentence(app, monkeypatch):
    c = TestClient(app, base_url="https://testserver")

    def refuse(self, access_key, secret_key):
        from botocore.exceptions import ClientError

        class Refusing:
            def get_object(self, **kw):
                raise ClientError(
                    {"Error": {"Code": "SignatureDoesNotMatch"}}, "GetObject"
                )

        return Refusing()

    monkeypatch.setattr(ClientCache, "build", refuse)
    r = login(c)
    assert r.status_code == 401
    assert "user name or password" in r.json()["detail"]


def test_not_found_or_denied_on_the_probe_means_the_keys_are_valid(app, monkeypatch):
    from botocore.exceptions import ClientError

    for code in ("404", "AccessDenied", "NoSuchKey", "NotFound", "InvalidRange"):

        class Answering:
            def get_object(self, **kw):
                raise ClientError({"Error": {"Code": code}}, "GetObject")

        monkeypatch.setattr(ClientCache, "build", lambda self, a, s: Answering())
        c = TestClient(app, base_url="https://testserver")
        assert login(c).status_code == 204, code


def test_an_unreachable_store_is_502(app, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    class Down:
        def get_object(self, **kw):
            raise EndpointConnectionError(endpoint_url="https://store")

    monkeypatch.setattr(ClientCache, "build", lambda self, a, s: Down())
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
    assert r.headers["set-cookie"].startswith(f'__Host-{COOKIE}=""')
    assert "max-age=0" in r.headers["set-cookie"].lower()
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


def test_a_bare_403_or_400_is_not_a_login(app, monkeypatch):
    from botocore.exceptions import ClientError

    for code in ("403", "400"):

        class Bare:
            def get_object(self, **kw):
                raise ClientError({"Error": {"Code": code}}, "GetObject")

        monkeypatch.setattr(ClientCache, "build", lambda self, a, s: Bare())
        c = TestClient(app, base_url="https://testserver")
        r = login(c)
        assert r.status_code == 502, code
        assert "set-cookie" not in r.headers
        assert c.get("/results/_session").status_code == 401


def test_a_wrong_password_leaves_the_cache_alone(cfg, monkeypatch):
    from botocore.exceptions import ClientError

    class Refusing:
        def get_object(self, **kw):
            raise ClientError({"Error": {"Code": "SignatureDoesNotMatch"}}, "GetObject")

    monkeypatch.setattr(ClientCache, "build", lambda self, a, s: Refusing())
    cache = ClientCache(cfg)
    app = create_results_app(cfg, SessionCodec(KEY, hours=8), clients=cache)
    c = TestClient(app, base_url="https://testserver")
    assert login(c).status_code == 401
    assert len(cache) == 0


def _xff_app(cfg, hops, limiter):
    cfg = cfg.model_copy(update={"trusted_hops": hops})
    return TestClient(
        create_results_app(cfg, SessionCodec(KEY, hours=8), limiter=limiter),
        base_url="https://testserver",
    )


def test_rotating_the_left_most_forwarded_for_does_not_escape(cfg):
    limiter = LoginLimiter()
    for _ in range(5):
        limiter.failed("9.9.9.9")
    c = _xff_app(cfg, 1, limiter)
    for i in range(3):
        h = {**ORIGIN, "X-Forwarded-For": f"10.0.0.{i}, 9.9.9.9"}
        r = login(c, headers=h)
        assert r.status_code == 429
        assert "from this address" in r.json()["detail"]


def test_two_hops_pick_the_second_from_right(cfg):
    limiter = LoginLimiter()
    for _ in range(5):
        limiter.failed("7.7.7.7")
    c = _xff_app(cfg, 2, limiter)
    h = {**ORIGIN, "X-Forwarded-For": "1.1.1.1, 7.7.7.7, 8.8.8.8"}
    assert login(c, headers=h).status_code == 429


def test_a_short_forwarded_list_falls_back_to_the_peer(cfg):
    limiter = LoginLimiter()
    for _ in range(5):
        limiter.failed("testclient")
    c = _xff_app(cfg, 2, limiter)
    h = {**ORIGIN, "X-Forwarded-For": "1.1.1.1"}
    assert login(c, headers=h).status_code == 429


def test_ten_failures_for_one_user_block_it_from_any_address(cfg, monkeypatch):
    from botocore.exceptions import ClientError

    class Refusing:
        def get_object(self, **kw):
            raise ClientError({"Error": {"Code": "InvalidAccessKeyId"}}, "GetObject")

    monkeypatch.setattr(ClientCache, "build", lambda self, a, s: Refusing())
    c = _xff_app(cfg, 1, LoginLimiter(max_failures=1000))
    for i in range(10):
        h = {**ORIGIN, "X-Forwarded-For": f"5.5.5.{i}"}
        assert login(c, headers=h).status_code == 401
    h = {**ORIGIN, "X-Forwarded-For": "5.5.5.99"}
    r = login(c, headers=h)
    assert r.status_code == 429
    assert "for this user" in r.json()["detail"]
    assert "five minutes" in r.json()["detail"]


def test_the_limiter_is_bounded_and_reads_add_nothing():
    lim = LoginLimiter(max_keys=3)
    assert not lim.blocked("unknown")
    assert len(lim) == 0
    for k in "abcd":
        lim.failed(k)
    assert len(lim) == 3
    assert not lim.blocked("a")


def test_eviction_is_least_recently_failed():
    lim = LoginLimiter(max_keys=2)
    lim.failed("a")
    lim.failed("b")
    lim.failed("a")
    lim.failed("c")
    assert "b" not in lim._fails
    assert "a" in lim._fails and "c" in lim._fails
