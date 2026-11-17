"""The CLI's side of the campaigns API: the address rule, the stored login,
and requests that carry the token."""

from __future__ import annotations

import importlib.metadata
import os
import stat
from dataclasses import replace

import httpx
import pytest

from htrflow_converter.remote import (
    CLIENT_HEADER,
    ENV_URL,
    Login,
    NotSignedIn,
    Remote,
    RemoteError,
    checked_address,
    forget,
    load,
    login_path,
    remote,
    save,
)


def ENV(tmp_path):
    return {"XDG_CONFIG_HOME": str(tmp_path)}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


LOGIN = Login(
    server="https://h.example",
    issuer="https://idp.example",
    client_id="htrflow-cli",
    token_endpoint="https://idp.example/token",
    access_token="ACCESS-SECRET",
    refresh_token="REFRESH-SECRET",
    expires_at=4_000_000_000.0,
)
SECRETS = ("ACCESS-SECRET", "REFRESH-SECRET", "NEW-ACCESS", "NEW-REFRESH")


def clean(e: RemoteError) -> None:
    """No error text carries a token or the Authorization header."""
    for text in (str(e), e.detail, e.hint, repr(e)):
        for secret in SECRETS:
            assert secret not in text
        assert "Bearer" not in text


@pytest.mark.parametrize(
    "url, out",
    [
        ("https://h.example", "https://h.example"),
        ("https://h.example/control/", "https://h.example/control"),
        ("http://localhost:8083/control", "http://localhost:8083/control"),
        ("http://127.0.0.1:8083", "http://127.0.0.1:8083"),
        ("http://127.5.6.7", "http://127.5.6.7"),
        ("http://[::1]:8083/", "http://[::1]:8083"),
    ],
)
def test_an_https_or_loopback_address_is_accepted_and_normalised(url, out):
    assert checked_address(url, "the server") == out


@pytest.mark.parametrize(
    "url",
    [
        "http://h.example",
        "h.example",
        "ftp://h.example",
        "http://localhost.example",
        "http://notlocalhost",
        "http://128.0.0.1",
        "https://",
        "",
        "https://user:pw@h.example",
        "https://user@h.example",
        "https://h.example/?a=b",
        "https://h.example/#frag",
        "https://h.example:notaport",
    ],
)
def test_any_other_address_is_refused_naming_what_it_was(url):
    with pytest.raises(RemoteError, match="the server") as e:
        checked_address(url, "the server")
    assert "pw" not in str(e.value)


def test_a_saved_login_is_read_back_and_only_the_user_can_read_it(tmp_path):
    old = os.umask(0o022)
    try:
        save(LOGIN, ENV(tmp_path))
    finally:
        os.umask(old)
    path = login_path(ENV(tmp_path))
    assert load(ENV(tmp_path)) == LOGIN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_login_path_falls_back_to_the_home_config(tmp_path):
    assert login_path({"HOME": str(tmp_path)}) == (
        tmp_path / ".config" / "htrflow" / "login.json"
    )


@pytest.mark.parametrize("content", [None, "", "{", "[]", '{"server": "https://h"}'])
def test_no_file_or_a_broken_one_is_not_signed_in(tmp_path, content):
    if content is not None:
        p = login_path(ENV(tmp_path))
        p.parent.mkdir(parents=True, mode=0o700)
        p.write_text(content)
        p.chmod(0o600)
    with pytest.raises(NotSignedIn) as e:
        load(ENV(tmp_path))
    assert "htrflow-campaigns login" in e.value.hint
    assert "read by others" not in e.value.detail


def test_a_pathologically_nested_file_is_not_signed_in(tmp_path):
    save(LOGIN, ENV(tmp_path))
    login_path(ENV(tmp_path)).write_text("[" * 100_000)
    with pytest.raises(NotSignedIn):
        load(ENV(tmp_path))


@pytest.mark.parametrize(
    "which, mode", [("file", 0o644), ("file", 0o640), ("dir", 0o755), ("dir", 0o750)]
)
def test_a_login_others_could_read_is_not_used(tmp_path, which, mode):
    save(LOGIN, ENV(tmp_path))
    p = login_path(ENV(tmp_path))
    (p if which == "file" else p.parent).chmod(mode)
    with pytest.raises(NotSignedIn) as e:
        load(ENV(tmp_path))
    assert (
        e.value.detail
        == "The stored sign-in could be read by others, so it is not used."
    )
    assert e.value.hint == "Sign in again: htrflow-campaigns login"
    clean(e.value)


def test_save_does_not_write_through_a_symlink_at_the_temporary_name(
    tmp_path, monkeypatch
):
    """The first name ``mkstemp`` tries is planted as a symlink to a file
    the user owns; an exclusive create refuses it and moves on, so the
    victim is untouched and the login lands."""
    import tempfile

    p = login_path(ENV(tmp_path))
    p.parent.mkdir(parents=True, mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    planted = p.parent / ".login.json.planted"
    planted.symlink_to(victim)
    names = iter(["planted", "fresh"])
    monkeypatch.setattr(tempfile, "_get_candidate_names", lambda: names)
    save(LOGIN, ENV(tmp_path))
    assert victim.read_text() == "keep"
    assert planted.is_symlink() and planted.readlink() == victim
    assert load(ENV(tmp_path)) == LOGIN


def test_a_path_that_does_not_start_with_a_slash_is_refused_before_any_request(
    tmp_path,
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    r = Remote(LOGIN, client_for(handler), ENV(tmp_path))
    for path in ("@evil.example/v1", "v1/whoami", ""):
        with pytest.raises(ValueError):
            r.get(path)
    assert seen == []


@pytest.mark.parametrize("expires_in", ["inf", "nan", -5, 0, "abc", None, [1]])
def test_an_odd_expires_in_is_clamped_to_a_minute(tmp_path, expires_in):
    def handler(request):
        if request.url == httpx.URL(LOGIN.token_endpoint):
            return httpx.Response(
                200, json={"access_token": "NEW-ACCESS", "expires_in": expires_in}
            )
        return httpx.Response(200, json={})

    r = Remote(
        replace(LOGIN, expires_at=5.0),
        client_for(handler),
        ENV(tmp_path),
        now=lambda: 100.0,
    )
    r.get("/v1/whoami")
    assert r.login.expires_at == 160.0


def _proxied(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.example:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")


@pytest.mark.parametrize("server", ["http://localhost:8083", "http://127.0.0.1:8083"])
def test_plain_http_never_goes_through_a_proxy_but_https_keeps_it(
    tmp_path, monkeypatch, server
):
    _proxied(monkeypatch)
    save(replace(LOGIN, server=server), ENV(tmp_path))
    c = remote(ENV(tmp_path))._client
    # leans on httpx's private transport selection
    for url in (server + "/v1/whoami", "http://idp.example/token"):
        assert (
            c._transport_for_url(httpx.URL(url))
            is c._mounts[next(k for k in c._mounts if k.pattern == "http://")]
        )
        assert c._transport_for_url(httpx.URL(url))._pool.__class__.__name__ == (
            "ConnectionPool"
        )
    assert c._transport_for_url(
        httpx.URL("https://h.example")
    )._pool.__class__.__name__ == ("HTTPProxy")


def test_a_login_file_with_a_plain_http_server_is_not_signed_in(tmp_path):
    save(replace(LOGIN, server="http://h.example"), ENV(tmp_path))
    with pytest.raises(NotSignedIn):
        load(ENV(tmp_path))


def test_save_replaces_a_file_others_could_read(tmp_path):
    p = login_path(ENV(tmp_path))
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    p.chmod(0o644)
    save(LOGIN, ENV(tmp_path))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert [f.name for f in p.parent.iterdir()] == ["login.json"]


def test_forget_removes_the_file_and_says_whether_there_was_one(tmp_path):
    assert forget(ENV(tmp_path)) is False
    save(LOGIN, ENV(tmp_path))
    assert forget(ENV(tmp_path)) is True
    assert not login_path(ENV(tmp_path)).exists()


@pytest.mark.parametrize("server", ["https://h.example", "https://h.example/control"])
def test_requests_go_to_the_prefix_once(tmp_path, server):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"user": "ada"})

    r = Remote(replace(LOGIN, server=server), client_for(handler), ENV(tmp_path))
    r.get("/v1/whoami")
    assert seen == [f"{server}/v1/whoami"]


def test_a_request_carries_the_token_and_the_client_version(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"user": "ada"})

    r = Remote(LOGIN, client_for(handler), ENV(tmp_path))
    assert r.get("/v1/whoami", a="b") == {"user": "ada"}
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer " + LOGIN.access_token
    assert request.headers[CLIENT_HEADER] == importlib.metadata.version(
        "htrflow-converter"
    )
    assert request.url.params["a"] == "b"


def test_an_expiring_token_is_refreshed_first_and_the_new_one_saved(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url == httpx.URL(LOGIN.token_endpoint):
            return httpx.Response(
                200,
                json={
                    "access_token": "NEW-ACCESS",
                    "refresh_token": "NEW-REFRESH",
                    "expires_in": 600,
                },
            )
        return httpx.Response(200, json={"user": "ada"})

    now = 1000.0
    r = Remote(
        replace(LOGIN, expires_at=now + 10),
        client_for(handler),
        ENV(tmp_path),
        now=lambda: now,
    )
    r.get("/v1/whoami")
    assert [str(q.url) for q in seen] == [
        LOGIN.token_endpoint,
        "https://h.example/v1/whoami",
    ]
    form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": "REFRESH-SECRET",
        "client_id": "htrflow-cli",
    }
    assert seen[1].headers["Authorization"] == "Bearer NEW-ACCESS"
    saved = load(ENV(tmp_path))
    assert (saved.access_token, saved.refresh_token) == ("NEW-ACCESS", "NEW-REFRESH")
    assert saved.expires_at == now + 600


def test_a_provider_that_sends_no_new_refresh_token_keeps_the_old_one(tmp_path):
    def handler(request):
        if request.url == httpx.URL(LOGIN.token_endpoint):
            return httpx.Response(200, json={"access_token": "NEW-ACCESS"})
        return httpx.Response(200, json={})

    r = Remote(
        replace(LOGIN, expires_at=5.0),
        client_for(handler),
        ENV(tmp_path),
        now=lambda: 0.0,
    )
    r.get("/v1/whoami")
    assert load(ENV(tmp_path)).refresh_token == "REFRESH-SECRET"


def test_a_login_that_cannot_be_saved_still_makes_the_request(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url == httpx.URL(LOGIN.token_endpoint):
            return httpx.Response(200, json={"access_token": "NEW-ACCESS"})
        return httpx.Response(200, json={"user": "ada"})

    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    r = Remote(
        replace(LOGIN, expires_at=5.0),
        client_for(handler),
        {"XDG_CONFIG_HOME": str(blocked)},
        now=lambda: 0.0,
    )
    assert r.get("/v1/whoami") == {"user": "ada"}
    assert seen[-1].headers["Authorization"] == "Bearer NEW-ACCESS"


def test_a_refresh_is_never_sent_to_a_plain_http_token_endpoint(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    r = Remote(
        replace(LOGIN, token_endpoint="http://idp.example/token", expires_at=5.0),
        client_for(handler),
        ENV(tmp_path),
        now=lambda: 0.0,
    )
    with pytest.raises(RemoteError, match="the sign-in service") as e:
        r.get("/v1/whoami")
    assert seen == []
    clean(e.value)


def test_a_refused_refresh_or_a_401_is_not_signed_in(tmp_path):
    def refuses(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    r = Remote(
        replace(LOGIN, expires_at=5.0),
        client_for(refuses),
        ENV(tmp_path),
        now=lambda: 0.0,
    )
    with pytest.raises(NotSignedIn) as e:
        r.get("/v1/whoami")
    assert "htrflow-campaigns login" in e.value.hint
    clean(e.value)

    def unauthorised(request):
        return httpx.Response(401, json={"detail": "d", "hint": "h"})

    r = Remote(LOGIN, client_for(unauthorised), ENV(tmp_path))
    with pytest.raises(NotSignedIn) as e:
        r.get("/v1/whoami")
    assert e.value.status == 401
    clean(e.value)


def test_an_expiring_token_without_a_refresh_token_is_not_signed_in(tmp_path):
    r = Remote(
        replace(LOGIN, expires_at=5.0, refresh_token=""),
        client_for(lambda request: httpx.Response(200, json={})),
        ENV(tmp_path),
        now=lambda: 0.0,
    )
    with pytest.raises(NotSignedIn) as e:
        r.get("/v1/whoami")
    clean(e.value)


def test_a_refusal_carries_the_servers_two_sentences(tmp_path):
    r = Remote(
        LOGIN,
        client_for(
            lambda request: httpx.Response(403, json={"detail": "d", "hint": "h"})
        ),
        ENV(tmp_path),
    )
    with pytest.raises(RemoteError) as e:
        r.get("/v1/whoami")
    assert (e.value.detail, e.value.hint, e.value.status) == ("d", "h", 403)
    clean(e.value)


def test_a_body_that_is_not_ours_still_gives_a_sentence(tmp_path):
    r = Remote(
        LOGIN,
        client_for(
            lambda request: httpx.Response(
                502,
                text="<html>bad gateway</html>",
                headers={"content-type": "text/html"},
            )
        ),
        ENV(tmp_path),
    )
    with pytest.raises(RemoteError) as e:
        r.get("/v1/whoami")
    assert e.value.detail == "the server answered 502"
    assert e.value.status == 502
    clean(e.value)


def test_a_redirect_is_an_error_not_followed(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    r = Remote(LOGIN, client_for(handler), ENV(tmp_path))
    with pytest.raises(RemoteError, match="redirect") as e:
        r.get("/v1/whoami")
    assert len(seen) == 1
    clean(e.value)


def test_an_unreachable_server_is_a_sentence_not_a_traceback(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom Bearer ACCESS-SECRET")

    r = Remote(LOGIN, client_for(handler), ENV(tmp_path))
    with pytest.raises(RemoteError) as e:
        r.get("/v1/whoami")
    assert "https://h.example" in e.value.detail
    clean(e.value)


@pytest.mark.parametrize(
    "answer", [httpx.Response(200, json=[1]), httpx.Response(200, text="<p>")]
)
def test_a_success_that_is_not_an_object_is_an_error(tmp_path, answer):
    r = Remote(LOGIN, client_for(lambda request: answer), ENV(tmp_path))
    with pytest.raises(RemoteError) as e:
        r.get("/v1/whoami")
    clean(e.value)


def test_the_environment_address_must_be_the_one_signed_in_to(tmp_path):
    save(LOGIN, ENV(tmp_path))
    with pytest.raises(NotSignedIn) as e:
        remote({**ENV(tmp_path), ENV_URL: "https://other.example"})
    assert "https://other.example" in e.value.detail
    assert "https://h.example" in e.value.detail
    assert remote({**ENV(tmp_path), ENV_URL: "https://h.example/"}).login == LOGIN
    assert remote(ENV(tmp_path)).login == LOGIN


def test_remote_builds_a_client_with_a_timeout_and_no_redirects(tmp_path):
    save(LOGIN, ENV(tmp_path))
    c = remote(ENV(tmp_path))._client
    assert c.timeout == httpx.Timeout(30.0)
    assert c.follow_redirects is False
