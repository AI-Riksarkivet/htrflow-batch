"""The device sign-in against a fake provider."""

from urllib.parse import parse_qsl

import httpx
import pytest

from htrflow_converter import signin
from htrflow_converter.remote import RemoteError
from htrflow_converter.signin import device_login, too_old

SERVER = "https://h.example"
ISSUER = "https://id.example/realm"
DEVICE_URL = "https://id.example/device"
TOKEN_URL = "https://id.example/token"
PENDING = (400, {"error": "authorization_pending"})
TOKENS = (
    200,
    {"access_token": "AT-secret", "refresh_token": "RT-secret", "expires_in": 300},
)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class Provider:
    """/v1/login, discovery, the device endpoint and the token endpoint."""

    def __init__(self, tokens, **over):
        self.tokens = list(tokens)
        self.login = {
            "issuer": ISSUER,
            "clientId": "cli",
            "audience": "api",
            "serverVersion": "1.0.0",
            "minClient": "0.0.1",
        } | over.get("login", {})
        self.discovery = {
            "issuer": ISSUER,
            "device_authorization_endpoint": DEVICE_URL,
            "token_endpoint": TOKEN_URL,
        } | over.get("discovery", {})
        self.device = {
            "device_code": "DC-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://id.example/activate",
            "verification_uri_complete": "https://id.example/activate?code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 5,
        } | over.get("device", {})
        for key in [k for k, v in self.discovery.items() if v is None]:
            del self.discovery[key]
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        url = str(request.url)
        if url == f"{SERVER}/v1/login":
            return httpx.Response(200, json=self.login)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        if url == DEVICE_URL:
            return httpx.Response(200, json=self.device)
        if url == TOKEN_URL:
            item = self.tokens.pop(0)
            if isinstance(item, Exception):
                raise item
            return httpx.Response(item[0], json=item[1])
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))

    def sent(self, url):
        return [r for r in self.requests if str(r.url) == url]


def run(provider, clock=None, said=None):
    clock = clock or Clock()
    said = [] if said is None else said
    return device_login(SERVER, provider.client(), said.append, clock.sleep, clock.now)


def form(request):
    return dict(parse_qsl(request.content.decode(), strict_parsing=True))


def test_a_sign_in_prints_the_link_and_the_code_and_returns_the_login():
    provider, clock, said = Provider([PENDING, TOKENS]), Clock(), []
    login = run(provider, clock, said)
    text = "\n".join(said)
    assert "https://id.example/activate?code=ABCD-EFGH" in text
    assert "ABCD-EFGH" in text
    assert "DC-secret" not in text and "AT-secret" not in text
    assert form(provider.sent(DEVICE_URL)[0]) == {"client_id": "cli", "scope": "openid"}
    assert form(provider.sent(TOKEN_URL)[0]) == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "DC-secret",
        "client_id": "cli",
    }
    assert login.server == SERVER and login.issuer == ISSUER
    assert login.client_id == "cli" and login.token_endpoint == TOKEN_URL
    assert (login.access_token, login.refresh_token) == ("AT-secret", "RT-secret")
    assert login.expires_at == clock.now() + 300


def test_without_a_complete_link_it_prints_the_uri_and_the_code():
    provider = Provider([TOKENS], device={"verification_uri_complete": None})
    said = []
    run(provider, said=said)
    assert said == ["Open https://id.example/activate and enter the code ABCD-EFGH."]


def test_a_missing_refresh_token_is_stored_empty():
    login = run(Provider([(200, {"access_token": "AT", "expires_in": 60})]))
    assert login.refresh_token == ""


def test_it_waits_while_the_person_has_not_answered():
    clock = Clock()
    run(Provider([PENDING, PENDING, TOKENS]), clock)
    assert clock.slept == [5, 5, 5]


# Review Focus 5
def test_slow_down_lengthens_the_wait_by_five_seconds_and_carries_on():
    clock = Clock()
    run(Provider([PENDING, (400, {"error": "slow_down"}), TOKENS]), clock)
    assert clock.slept == [5, 5, 10]


@pytest.mark.parametrize(
    "error, word", [("expired_token", "expired"), ("access_denied", "denied")]
)
def test_expiry_and_denial_end_with_one_sentence(error, word):
    with pytest.raises(RemoteError) as e:
        run(Provider([(400, {"error": error})]))
    assert word in e.value.detail
    assert e.value.detail.count(".") == 1


def test_the_code_running_out_ends_the_wait():
    clock = Clock()
    provider = Provider([PENDING] * 20, device={"expires_in": 15})
    with pytest.raises(RemoteError) as e:
        run(provider, clock)
    assert "expired" in e.value.detail
    assert clock.slept == [5, 5, 5]


def test_a_provider_that_names_another_issuer_is_refused():
    provider = Provider([TOKENS], discovery={"issuer": "https://evil.example"})
    with pytest.raises(RemoteError) as e:
        run(provider)
    assert "https://evil.example" in e.value.detail and ISSUER in e.value.detail
    assert provider.sent(DEVICE_URL) == []


def test_a_trailing_slash_on_either_issuer_is_the_same_issuer():
    run(Provider([TOKENS], discovery={"issuer": ISSUER + "/"}))


@pytest.mark.parametrize("field", ["device_authorization_endpoint", "token_endpoint"])
def test_a_provider_without_the_device_sign_in_is_refused(field):
    with pytest.raises(RemoteError) as e:
        run(Provider([TOKENS], discovery={field: None}))
    assert "device sign-in" in e.value.detail


def test_an_endpoint_that_is_not_https_or_loopback_is_refused():
    provider = Provider(
        [TOKENS], discovery={"token_endpoint": "http://id.example/token"}
    )
    with pytest.raises(RemoteError) as e:
        run(provider)
    assert "the sign-in service" in e.value.detail
    assert provider.sent(DEVICE_URL) == []


def test_a_verification_link_that_is_not_https_is_refused_before_it_is_shown():
    provider = Provider(
        [TOKENS], device={"verification_uri_complete": "http://id.example/a?c=1"}
    )
    said = []
    with pytest.raises(RemoteError):
        run(provider, said=said)
    assert said == [] and provider.sent(TOKEN_URL) == []


def test_a_client_older_than_the_servers_minimum_is_told_before_signing_in(monkeypatch):
    monkeypatch.setattr(signin, "_version", lambda: "1.2.3")
    provider = Provider([TOKENS], login={"minClient": "99.0.0"})
    with pytest.raises(RemoteError) as e:
        run(provider)
    assert "1.2.3" in e.value.detail and "99.0.0" in e.value.detail
    assert [str(r.url) for r in provider.requests] == [f"{SERVER}/v1/login"]


@pytest.mark.parametrize(
    "client, minimum, old",
    [
        ("1.2.3", "1.2.4", True),
        ("1.2.3", "1.2.3", False),
        ("1.10.0", "1.9.0", False),
        ("", "1.0.0", False),
        ("1.2.3.dev4", "1.2.4", True),
        ("1.2", "1.0.0", False),
        ("1.2.3", "next", False),
    ],
)
def test_too_old_reads_three_numbers_and_trusts_anything_else(client, minimum, old):
    assert too_old(client, minimum) is old


def test_an_unusual_error_ends_it_with_the_code_only_when_it_is_plain():
    with pytest.raises(RemoteError) as e:
        run(
            Provider(
                [(400, {"error": "invalid_grant", "error_description": "x" * 900})]
            )
        )
    assert "invalid_grant" in e.value.detail and len(e.value.detail) < 200
    with pytest.raises(RemoteError) as e:
        run(Provider([(400, {"error": "Evil Text AT-secret\n"})]))
    assert "Evil" not in e.value.detail and "secret" not in e.value.detail


def test_an_answer_that_is_not_json_ends_it_at_once():
    provider = Provider([])
    polls = []

    def handler(request):
        if str(request.url) == TOKEN_URL:
            polls.append(request)
            return httpx.Response(502, text="<html>bad gateway</html>")
        return provider(request)

    clock = Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RemoteError) as e:
        device_login(SERVER, client, lambda s: None, clock.sleep, clock.now)
    assert "<html>" not in e.value.detail and "cannot read" in e.value.detail
    assert len(polls) == 1 and clock.slept == [5]


def test_a_token_answer_without_an_access_token_is_refused():
    with pytest.raises(RemoteError):
        run(Provider([(200, {"access_token": "", "refresh_token": "RT-secret"})]))


def test_one_network_failure_in_the_wait_is_ridden_out():
    clock = Clock()
    run(Provider([httpx.ConnectError("down"), TOKENS]), clock)
    assert clock.slept == [5, 5]


def test_three_network_failures_in_a_row_end_it():
    down = httpx.ConnectError("down AT-secret")
    with pytest.raises(RemoteError) as e:
        run(Provider([down, down, down, TOKENS]))
    assert "secret" not in str(e.value)


def test_failures_that_are_not_in_a_row_do_not_add_up():
    down = httpx.ConnectError("down")
    run(Provider([down, down, PENDING, down, down, TOKENS]))


def test_the_server_must_be_https_or_loopback():
    with pytest.raises(RemoteError):
        device_login("http://h.example", Provider([]).client(), print)


def test_a_device_endpoint_refusal_is_the_sign_in_services_with_its_code_only():
    class P(Provider):
        def __call__(self, request):
            if str(request.url) == DEVICE_URL:
                return httpx.Response(401, json={"error": "invalid_client"})
            return super().__call__(request)

    with pytest.raises(RemoteError) as e:
        run(P([TOKENS]))
    assert "sign-in service" in e.value.detail and "401" in e.value.detail
    assert "invalid_client" in e.value.detail and "the server" not in e.value.detail


def test_nothing_else_of_a_provider_body_is_shown():
    class P(Provider):
        def __call__(self, request):
            if str(request.url) == DEVICE_URL:
                return httpx.Response(
                    400,
                    json={"detail": "\x1b[31mRED" + "y" * 300, "error": "Not Plain!"},
                )
            return super().__call__(request)

    with pytest.raises(RemoteError) as e:
        run(P([TOKENS]))
    text = e.value.detail + e.value.hint
    assert "\x1b" not in text and "RED" not in text and "yyy" not in text
    assert "Not Plain" not in text and "400" in text


def test_a_discovery_issuer_with_an_escape_is_not_echoed_raw():
    provider = Provider([TOKENS], discovery={"issuer": "\x1b]0;pwned\x07https://evil"})
    with pytest.raises(RemoteError) as e:
        run(provider)
    assert "\x1b" not in e.value.detail and "\x07" not in e.value.detail
    assert len(e.value.detail) < 500


@pytest.mark.parametrize("given", [0, -3, 0.001, "NaN", True, "1", None, 4.9, 1e9, 61])
def test_the_wait_is_never_under_five_seconds_nor_over_sixty(given):
    clock = Clock()
    provider = Provider([PENDING] * 1000, device={"interval": given, "expires_in": 600})
    with pytest.raises(RemoteError):
        run(provider, clock)
    assert min(clock.slept) >= 5 and max(clock.slept) <= 60
    assert len(provider.sent(TOKEN_URL)) <= 600 / 5 + 1


def test_slow_down_adds_five_and_stops_at_sixty():
    clock = Clock()
    slow = (400, {"error": "slow_down"})
    run(Provider([slow] * 14 + [TOKENS]), clock)
    assert clock.slept[:4] == [5, 10, 15, 20]
    assert max(clock.slept) == 60 and clock.slept[-1] == 60
