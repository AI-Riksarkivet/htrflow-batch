"""The CLI's real `Remote` and device sign-in against the real campaigns API.

Each side's own tests run against a fake of the other; this module runs
the two together, so a field renamed or a sentence moved on one side fails
here. The converter's source never imports `htrflow_api`; its tests may.

The api's test helper `api_fakes` (the `Signer` that makes tokens) lives in
packages/api/tests, which is not on `sys.path` under
`--import-mode=importlib`; it is put there below. Its name does not collide
with this directory's `fakes`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "tests"))
from api_fakes import REQUIRED, Signer  # noqa: E402
from htrflow_api import MIN_CLIENT  # noqa: E402
from htrflow_api import app as api_app  # noqa: E402
from htrflow_api.config import ApiConfig  # noqa: E402
from htrflow_api.tokens import Verifier  # noqa: E402

from htrflow_converter import remote as cli_remote  # noqa: E402
from htrflow_converter import signin  # noqa: E402
from htrflow_converter.remote import (  # noqa: E402
    Login,
    NotSignedIn,
    Remote,
    RemoteError,
)

HOST = "campaigns.example"
SERVER = f"https://{HOST}/control"
ISSUER = REQUIRED["HTRFLOW_API_ISSUER"]
DEVICE_URL = "https://id.example/device"
TOKEN_URL = "https://id.example/token"


@pytest.fixture
def signer() -> Signer:
    return Signer()


@pytest.fixture
def service(signer: Signer) -> TestClient:
    cfg = ApiConfig.from_env({**REQUIRED, "HTRFLOW_API_PATH": "/control"})
    return TestClient(api_app.create_app(cfg, Verifier(cfg, signer.jwks)))


def to_service(service: TestClient, request: httpx.Request) -> httpx.Response:
    """One request the CLI made, answered by the real app."""
    answer = service.request(
        request.method,
        str(request.url),
        headers=dict(request.headers),
        content=request.content,
    )
    return httpx.Response(
        answer.status_code, headers=answer.headers, content=answer.content
    )


def client_for(service: TestClient, provider=None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == HOST:
            return to_service(service, request)
        assert provider is not None, f"unexpected request to {request.url}"
        return provider(request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def login_with(token: str) -> Login:
    return Login(
        server=SERVER,
        issuer=ISSUER,
        client_id=REQUIRED["HTRFLOW_API_CLIENT_ID"],
        token_endpoint=TOKEN_URL,
        access_token=token,
        refresh_token="",
        expires_at=4_000_000_000.0,
    )


def session(service: TestClient, token: str, tmp_path: Path) -> Remote:
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    return Remote(login_with(token), client_for(service), env)


def test_whoami_answers_every_field_the_cli_prints(service, signer, tmp_path):
    body = session(service, signer.token(), tmp_path).get("/v1/whoami")
    assert body == {
        "user": "ada",
        "groups": ["htr-operators"],
        "namespace": REQUIRED["HTRFLOW_API_NAMESPACE"],
        "queue": REQUIRED["HTRFLOW_API_QUEUE"],
        "serverVersion": "dev",
    }


def test_a_refused_token_is_not_signed_in_with_the_services_sentences(
    service, tmp_path
):
    with pytest.raises(NotSignedIn) as e:
        session(service, Signer(kid="other").token(), tmp_path).get("/v1/whoami")
    assert e.value.status == 401
    assert e.value.detail == (
        "The token was signed with a key this service does not know."
    )
    assert e.value.hint == "Sign in again: htrflow-campaigns login"


def test_a_403_is_a_remote_error_with_the_services_two_sentences(
    service, signer, tmp_path
):
    token = signer.token(groups=["other"])
    with pytest.raises(RemoteError) as e:
        session(service, token, tmp_path).get("/v1/whoami")
    assert not isinstance(e.value, NotSignedIn)
    assert e.value.status == 403
    assert e.value.detail == "You are not in the group htr-operators."
    assert e.value.hint.startswith("Ask whoever runs the sign-in service")


def test_an_old_client_gets_the_426_sentences(service, signer, tmp_path, monkeypatch):
    monkeypatch.setattr(cli_remote, "_version", lambda: "0.0.1")
    with pytest.raises(RemoteError) as e:
        session(service, signer.token(), tmp_path).get("/v1/whoami")
    assert e.value.status == 426
    assert e.value.detail == (
        f"This htrflow-campaigns is version 0.0.1 and the server needs at least "
        f"{MIN_CLIENT}."
    )
    assert e.value.hint == "Upgrade htrflow-campaigns, then try again."


class Reading(dict):
    """A dict that remembers which keys were read."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.read: set[str] = set()

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)


def provider_for(signer: Signer):
    """The sign-in service's side, enough for one device sign-in."""

    def provider(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "device_authorization_endpoint": DEVICE_URL,
                    "token_endpoint": TOKEN_URL,
                },
            )
        if url == DEVICE_URL:
            return httpx.Response(
                200,
                json={
                    "device_code": "DC",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://id.example/activate",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if url == TOKEN_URL:
            return httpx.Response(
                200, json={"access_token": signer.token(), "expires_in": 300}
            )
        return httpx.Response(404)

    return provider


def test_the_sign_in_reads_only_what_the_login_call_holds_and_then_works(
    service, signer, tmp_path, monkeypatch
):
    seen: list[Reading] = []
    real = signin._object

    def spy(response, what):
        body = real(response, what)
        if what == "the server":
            body = Reading(body)
            seen.append(body)
        return body

    monkeypatch.setattr(signin, "_object", spy)
    client = client_for(service, provider_for(signer))
    login = signin.device_login(
        SERVER, client, lambda line: None, lambda s: None, lambda: 1000.0
    )
    [answer] = seen
    assert set(answer) == {
        "issuer",
        "clientId",
        "audience",
        "serverVersion",
        "minClient",
    }
    assert answer.read == {"issuer", "clientId", "minClient"}
    assert (login.issuer, login.client_id) == (ISSUER, "htrflow-cli")
    # The token that sign-in got is one the service takes.
    remote = Remote(login, client, {"XDG_CONFIG_HOME": str(tmp_path)}, lambda: 1000.0)
    assert remote.get("/v1/whoami")["user"] == "ada"


def test_both_sides_name_the_client_header_the_same():
    assert cli_remote.CLIENT_HEADER == api_app.CLIENT_HEADER


VERSIONS = [
    ("0.13.0", "0.14.0"),
    ("0.14.0", "0.14.0"),
    ("1.10.0", "1.9.0"),
    ("1.2.3.dev4", "1.2.4"),
    ("0.14.0rc1", "0.14.0"),
    ("1.2", "1.0.0"),
    ("", "1.0.0"),
    ("dev", "1.0.0"),
    ("1.2.3", "next"),
    ("1.2.x", "1.2.3"),
    ("9" * 5000 + ".0.0", "1.0.0"),
    ("0.0.0", "1234567890.0.0"),
    ("0.0.0", "123456789.0.0"),
    ("٣.0.0", "4.0.0"),
]


@pytest.mark.parametrize(
    "client, minimum", VERSIONS, ids=[f"{c[:12]}<{m[:16]}" for c, m in VERSIONS]
)
def test_both_sides_read_versions_the_same(client: str, minimum: str):
    assert api_app.too_old(client, minimum) is signin.too_old(client, minimum)
