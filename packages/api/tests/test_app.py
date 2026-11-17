import pytest
from api_fakes import Signer  # noqa: F401
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.responses import Response

from htrflow_api import MIN_CLIENT
from htrflow_api.app import CLIENT_HEADER, create_app, too_old
from htrflow_api.tokens import Verifier


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(cfg, signer):
    return TestClient(create_app(cfg, Verifier(cfg, signer.jwks)))


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_login_needs_no_token_and_holds_nothing_secret(client, cfg):
    assert client.get("/v1/login").json() == {
        "issuer": cfg.issuer,
        "clientId": "htrflow-cli",
        "audience": "htrflow-batch",
        "serverVersion": "dev",
        "minClient": MIN_CLIENT,
    }


def test_whoami_answers_the_signed_in_person(client, signer):
    body = client.get("/v1/whoami", headers=auth(signer.token())).json()
    assert body == {
        "user": "ada",
        "groups": ["htr-operators"],
        "namespace": "htr-batch",
        "queue": "htr-batch",
        "serverVersion": "dev",
    }


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": "bearer "},
    ],
)
def test_no_bearer_token_is_a_401_with_the_challenge_header(client, headers):
    r = client.get("/v1/whoami", headers=headers)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == "Bearer"
    assert set(r.json()) == {"detail", "hint"}


def test_a_lowercase_scheme_is_a_bearer_token(client, signer):
    r = client.get("/v1/whoami", headers={"Authorization": f"bearer {signer.token()}"})
    assert r.status_code == 200


def test_a_refusal_keeps_its_status_and_two_sentences(client, signer):
    r = client.get("/v1/whoami", headers=auth(signer.token(groups=["other"])))
    assert r.status_code == 403 and set(r.json()) == {"detail", "hint"}


PREFIXES = ["", "/control"]
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def prefixed(cfg, signer, path):
    c = cfg.model_copy(update={"path": path})
    return c, create_app(c, Verifier(c, signer.jwks))


@pytest.mark.parametrize("prefix", PREFIXES)
def test_registration_style_does_not_matter(cfg, signer, prefix):
    """Routes added after the fact, in every style, sit behind the guard."""
    cfg, app = prefixed(cfg, signer, prefix)
    v1, calls = f"{prefix}/v1", []

    @app.post(f"{v1}/x")
    def post_x():
        calls.append("post")
        return {}

    @app.delete(f"{v1}/x")
    def delete_x():
        calls.append("delete")
        return {}

    router = APIRouter()

    @router.get("/r")
    def routed():
        calls.append("router")
        return {}

    app.include_router(router, prefix=f"{v1}/rt")

    async def mounted(scope, receive, send):
        calls.append("mount")
        await Response("ok")(scope, receive, send)

    app.mount(f"{v1}/m", mounted)
    c = TestClient(app)
    hits = [
        ("POST", f"{v1}/x"),
        ("DELETE", f"{v1}/x"),
        ("GET", f"{v1}/rt/r"),
        ("GET", f"{v1}/m"),
        ("GET", f"{v1}/m/deeper"),
    ]
    for method, path in hits:
        assert c.request(method, path).status_code == 401, (method, path)
    assert calls == []
    for method, path in hits:
        r = c.request(
            method, path, headers=auth(signer.token()), follow_redirects=False
        )
        assert r.status_code in (200, 307), (method, path)
    assert {"post", "delete", "router", "mount"} <= set(calls)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("tail", ["/whoami", "/nope", "", "/"])
def test_every_method_and_path_under_v1_is_401_without_a_token(
    cfg, signer, prefix, method, tail
):
    _, app = prefixed(cfg, signer, prefix)
    assert TestClient(app).request(method, f"{prefix}/v1{tail}").status_code == 401


@pytest.mark.parametrize("prefix", PREFIXES)
def test_with_a_token_unknown_is_404_and_wrong_method_405(cfg, signer, prefix):
    _, app = prefixed(cfg, signer, prefix)
    c, h = TestClient(app), auth(signer.token())
    assert c.get(f"{prefix}/v1/nope", headers=h).status_code == 404
    assert c.post(f"{prefix}/v1/whoami", headers=h).status_code == 405


@pytest.mark.parametrize("prefix", PREFIXES)
def test_login_is_open_for_get_and_head_only(cfg, signer, prefix):
    _, app = prefixed(cfg, signer, prefix)
    c = TestClient(app)
    assert c.get(f"{prefix}/v1/login").status_code == 200
    assert c.head(f"{prefix}/v1/login").status_code == 405  # past the guard
    for m in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]:
        assert c.request(m, f"{prefix}/v1/login").status_code == 401, m


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize(
    "tail",
    ["/../v1/whoami", "/login/", "/login/../whoami", "/login;x", "/whoami;x"],
)
def test_path_tricks_never_reach_a_handler_without_a_token(cfg, signer, prefix, tail):
    _, app = prefixed(cfg, signer, prefix)
    calls = []

    @app.api_route(f"{prefix}/v1/{{rest:path}}", methods=METHODS)
    def spy(rest: str):
        calls.append(rest)
        return {}

    c = TestClient(app)
    for path in [f"{prefix}/v1{tail}", f"{prefix}//v1/whoami", f"{prefix}/V1/whoami"]:
        assert c.get(path).status_code in (401, 404), path
    assert calls == []


def test_the_prefix_is_served_and_the_bare_path_is_not(cfg, signer):
    app = create_app(
        cfg.model_copy(update={"path": "/control"}), Verifier(cfg, signer.jwks)
    )
    c = TestClient(app)
    assert c.get("/control/v1/login").status_code == 200
    assert c.get("/v1/login").status_code == 404
    assert c.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    "client_version, status",
    [
        ("0.13.0", 426),
        ("0.1.0", 426),
        (MIN_CLIENT, 200),
        ("9.0.0", 200),
        ("dev", 200),
        ("0.14.0.dev3", 200),
        ("", 200),
    ],
)
def test_a_client_older_than_the_minimum_is_refused_with_both_versions(
    client, signer, client_version, status
):
    headers = auth(signer.token()) | (
        {CLIENT_HEADER: client_version} if client_version else {}
    )
    r = client.get("/v1/whoami", headers=headers)
    assert r.status_code == status
    if status == 426:
        assert client_version in r.json()["detail"] and MIN_CLIENT in r.json()["detail"]
        assert set(r.json()) == {"detail", "hint"}


def test_an_old_client_is_refused_before_the_token_is_checked(client):
    r = client.get("/v1/whoami", headers={CLIENT_HEADER: "0.1.0"})
    assert r.status_code == 426


def test_too_old():
    assert too_old("0.13.0") and not too_old("0.13.1") and not too_old("x.y")
    assert too_old("1.2.3", "1.10.0") and not too_old("1.10.0", "1.9.9")


def test_answers_are_not_cached(client, signer):
    assert client.get("/v1/login").headers["Cache-Control"] == "no-store"
    assert client.get("/v1/whoami").headers["Cache-Control"] == "no-store"
    r = client.get("/v1/whoami", headers=auth(signer.token()))
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get("/v1/nothing").headers["Cache-Control"] == "no-store"


def test_an_unhandled_error_is_json_with_two_sentences(cfg):
    class Broken:
        def verify(self, token):
            raise RuntimeError("boom")

    c = TestClient(create_app(cfg, Broken()), raise_server_exceptions=False)
    r = c.get("/v1/whoami", headers=auth("x.y.z"))
    assert r.status_code == 500 and r.json()["detail"] == "internal error"
    assert set(r.json()) == {"detail", "hint"}
    assert "boom" not in r.text
    assert r.headers["Cache-Control"] == "no-store"


def test_another_client_error_is_not_an_internal_error(cfg, signer):
    from starlette.exceptions import HTTPException

    _, app = prefixed(cfg, signer, "")

    @app.get("/v1/bad")
    def bad():
        raise HTTPException(400)

    r = TestClient(app).get("/v1/bad", headers=auth(signer.token()))
    assert r.status_code == 400 and r.json()["detail"] != "internal error"
    assert set(r.json()) == {"detail", "hint"}
    assert r.headers["Cache-Control"] == "no-store"


def test_current_identity_fails_loudly_without_the_guard(cfg, signer):
    from fastapi import Depends

    from htrflow_api.app import current_identity

    _, app = prefixed(cfg, signer, "")

    @app.get("/open")
    def open_(who=Depends(current_identity)):
        return {}

    r = TestClient(app, raise_server_exceptions=False).get("/open")
    assert r.status_code == 500


def test_the_token_is_not_logged(client, signer, caplog):
    token = signer.token(aud="other")
    with caplog.at_level("DEBUG"):
        client.get("/v1/whoami", headers=auth(token))
    assert token not in caplog.text


@pytest.mark.parametrize("path", ["/v1/nothing", "/nothing", "/v1/whoami/x"])
def test_an_unknown_path_is_a_two_sentence_404(client, signer, path):
    r = client.get(path, headers=auth(signer.token()))
    assert r.status_code == 404 and set(r.json()) == {"detail", "hint"}


def test_a_wrong_method_is_a_two_sentence_405(client, signer):
    r = client.post("/v1/whoami", headers=auth(signer.token()))
    assert r.status_code == 405 and set(r.json()) == {"detail", "hint"}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_there_is_no_schema_or_docs_endpoint(cfg, signer, path):
    c = TestClient(
        create_app(
            cfg.model_copy(update={"path": "/control"}), Verifier(cfg, signer.jwks)
        )
    )
    assert c.get(path).status_code == 404
    assert c.get("/control" + path).status_code == 404


def test_main_exits_with_the_config_message(monkeypatch):
    from htrflow_api.__main__ import main

    for k in list(__import__("os").environ):
        if k.startswith("HTRFLOW_API_"):
            monkeypatch.delenv(k)
    with pytest.raises(SystemExit) as e:
        main()
    assert "HTRFLOW_API_ISSUER" in str(e.value)
