"""login, logout and whoami, with the sign-in and the remote faked."""

import json
import sys

import pytest

from htrflow_converter import cli, remote, signin
from htrflow_converter.remote import ENV_URL, Login, NotSignedIn, RemoteError

LOGIN = Login(
    server="https://h.example",
    issuer="https://id.example",
    client_id="cli",
    token_endpoint="https://id.example/token",
    access_token="AT-secret",
    refresh_token="RT-secret",
    expires_at=9e12,
)
BODY = {
    "user": "ada",
    "groups": ["htr", "admins"],
    "namespace": "htr-batch",
    "queue": "batch",
    "serverVersion": "1.2.3",
}


class FakeRemote:
    def __init__(self, body=BODY, error=None):
        self.body, self.error, self.paths = body, error, []

    def get(self, path, **params):
        self.paths.append(path)
        if self.error:
            raise self.error
        return self.body


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(ENV_URL, raising=False)
    return remote.login_path()


def sign_in_as(monkeypatch, login=LOGIN, fake=None, seen=None):
    def device_login(server, client, say, *a):
        if seen is not None:
            seen.append(server)
        say("Open https://id.example/activate")
        return login

    monkeypatch.setattr(signin, "device_login", device_login)
    monkeypatch.setattr(remote, "remote", lambda env=None: fake or FakeRemote())


def test_login_without_an_address_exits_2_and_says_both_ways(home, capsys):
    assert cli.main(["login"]) == 2
    err = capsys.readouterr().err
    assert "--server" in err and "HTRFLOW_API_URL" in err
    assert not home.exists()


def test_login_saves_and_says_who(home, monkeypatch, capsys):
    seen = []
    sign_in_as(monkeypatch, seen=seen)
    assert cli.main(["login", "--server", "https://h.example"]) == 0
    out = capsys.readouterr()
    assert "signed in to https://h.example as ada" in out.out
    assert "Open https://id.example/activate" in out.out
    assert "secret" not in out.out + out.err
    assert remote.load().access_token == "AT-secret" and seen == ["https://h.example"]


def test_login_takes_the_address_from_the_environment(home, monkeypatch, capsys):
    seen = []
    sign_in_as(monkeypatch, seen=seen)
    monkeypatch.setenv(ENV_URL, "https://env.example")
    assert cli.main(["login"]) == 0 and seen == ["https://env.example"]


def test_login_signs_in_again_over_a_stored_login(home, monkeypatch):
    remote.save(LOGIN)
    sign_in_as(monkeypatch, login=Login(**{**LOGIN.__dict__, "access_token": "NEW"}))
    assert cli.main(["login", "--server", "https://h.example"]) == 0
    assert remote.load().access_token == "NEW"


def test_login_refused_exits_1_and_saves_nothing(home, monkeypatch, capsys):
    def refuse(*a):
        raise RemoteError(
            "The sign-in was denied.", "Sign in again if that was a mistake."
        )

    monkeypatch.setattr(signin, "device_login", refuse)
    assert cli.main(["login", "--server", "https://h.example"]) == 1
    err = capsys.readouterr().err
    assert "denied." in err and "Sign in again if" in err
    assert not home.exists()


def test_login_interrupted_exits_130_and_saves_nothing(home, monkeypatch, capsys):
    def interrupt(*a):
        raise KeyboardInterrupt

    monkeypatch.setattr(signin, "device_login", interrupt)
    assert cli.main(["login", "--server", "https://h.example"]) == 130
    assert capsys.readouterr().err.strip() == "sign-in interrupted"
    assert not home.exists()


def test_whoami_prints_the_rows_and_json_prints_the_body(monkeypatch, capsys):
    fake = FakeRemote()
    monkeypatch.setattr(remote, "remote", lambda env=None: fake)
    monkeypatch.setattr(remote, "_version", lambda: "0.9.0")
    fake.login = LOGIN
    assert cli.main(["whoami"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split(":")[0] for ln in lines] == [
        "user",
        "groups",
        "namespace",
        "queue",
        "server",
        "server version",
        "client version",
    ]
    assert lines[0] == "user: ada" and lines[1] == "groups: htr, admins"
    assert lines[4] == "server: https://h.example"
    assert lines[5] == "server version: 1.2.3" and lines[6] == "client version: 0.9.0"
    assert cli.main(["whoami", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == BODY


def test_whoami_when_not_signed_in_exits_2_with_the_hint(home, capsys):
    assert cli.main(["whoami"]) == 2
    err = capsys.readouterr().err
    assert "sign-in" in err and "htrflow-campaigns login" in err


def test_whoami_refused_exits_1_with_both_sentences(monkeypatch, capsys):
    fake = FakeRemote(error=RemoteError("You may not.", "Ask for access.", 403))
    fake.login = LOGIN
    monkeypatch.setattr(remote, "remote", lambda env=None: fake)
    assert cli.main(["whoami"]) == 1
    assert capsys.readouterr().err.splitlines() == ["You may not.", "Ask for access."]


def test_whoami_with_an_expired_sign_in_exits_2(monkeypatch, capsys):
    fake = FakeRemote(error=NotSignedIn())
    fake.login = LOGIN
    monkeypatch.setattr(remote, "remote", lambda env=None: fake)
    assert cli.main(["whoami"]) == 2


def test_logout_removes_the_login_and_is_fine_without_one(home, capsys):
    remote.save(LOGIN)
    assert cli.main(["logout"]) == 0
    assert not home.exists() and "signed out" in capsys.readouterr().out
    assert cli.main(["logout"]) == 0
    assert "not signed in" in capsys.readouterr().out.lower()


def test_validate_does_not_import_the_sign_in(monkeypatch):
    for name in ("htrflow_converter.signin", "htrflow_converter.remote"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    from pathlib import Path

    good = Path(__file__).parent / "fixtures" / "good"
    assert cli.main(["validate", str(good)]) == 0
    assert "htrflow_converter.signin" not in sys.modules
