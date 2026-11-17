import pytest
from api_fakes import Signer  # noqa: F401
from fastapi.testclient import TestClient

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


def test_every_v1_route_but_login_refuses_without_a_token(client):
    # walks app.routes, so a route added later cannot forget the guard
    seen = 0
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if "/v1/" in path and not path.endswith("/v1/login"):
            seen += 1
            assert client.get(path).status_code == 401, path
    assert seen


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


def test_the_token_is_not_logged(client, signer, caplog):
    token = signer.token(aud="other")
    with caplog.at_level("DEBUG"):
        client.get("/v1/whoami", headers=auth(token))
    assert token not in caplog.text


@pytest.mark.parametrize("path", ["/v1/nothing", "/nothing", "/v1/whoami/x"])
def test_an_unknown_path_is_a_two_sentence_404(client, path):
    r = client.get(path)
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
