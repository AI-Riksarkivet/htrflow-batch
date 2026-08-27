"""The campaigns checkout without a git binary: dulwich against a local
repository fixture (built with dulwich too, so the suite needs no git)."""

import socket

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from htrflow_reconciler.gitrepo import (
    _redact,
    _remote_head,
    _split_credentials,
    checkout,
)


@pytest.fixture
def origin(tmp_path):
    path = tmp_path / "origin"
    path.mkdir()
    repo = porcelain.init(str(path))
    (path / "campaigns").mkdir()
    (path / "campaigns" / "a.yaml").write_text("pipeline: demo-v1\nvolumes: [R1]\n")
    porcelain.add(repo, [str(path / "campaigns" / "a.yaml")])
    porcelain.commit(repo, message=b"first", author=b"t <t@x>", committer=b"t <t@x>")
    repo.close()
    return path


def _commit(path, name, text):
    repo = Repo(str(path))
    (path / "campaigns" / name).write_text(text)
    porcelain.add(repo, [str(path / "campaigns" / name)])
    porcelain.commit(repo, message=b"more", author=b"t <t@x>", committer=b"t <t@x>")
    repo.close()


def test_fresh_clone_checks_out_the_tree(origin, tmp_path):
    dest = checkout(str(origin), tmp_path / "clone")
    assert (dest / ".git").exists()
    assert (dest / "campaigns" / "a.yaml").read_text().startswith("pipeline:")


def test_second_call_updates_to_the_remote_head(origin, tmp_path):
    dest = checkout(str(origin), tmp_path / "clone")
    _commit(origin, "b.yaml", "pipeline: demo-v1\nvolumes: [R2]\n")
    checkout(str(origin), dest)
    assert (dest / "campaigns" / "b.yaml").exists()


def test_update_follows_a_rewritten_history(origin, tmp_path):
    """fetch + hard reset, never a merge: a force-push on the campaigns repo
    must not wedge the CronJob's checkout forever."""
    dest = checkout(str(origin), tmp_path / "clone")
    # local edit that would conflict with a pull
    (dest / "campaigns" / "a.yaml").write_text("garbage")
    _commit(origin, "a.yaml", "pipeline: demo-v2\nvolumes: [R1]\n")
    checkout(str(origin), dest)
    assert "demo-v2" in (dest / "campaigns" / "a.yaml").read_text()


def test_errors_never_carry_the_credential(tmp_path):
    with pytest.raises(RuntimeError) as e:
        checkout("https://user:s3cret@127.0.0.1:1/x.git", tmp_path / "c", timeout=2)
    assert "s3cret" not in str(e.value)
    assert "user" not in str(e.value)


def test_timeout_is_applied_to_sockets_and_restored(origin, tmp_path, monkeypatch):
    seen = []
    real = porcelain.clone

    def spy(*a, **kw):
        seen.append(socket.getdefaulttimeout())
        return real(*a, **kw)

    monkeypatch.setattr(porcelain, "clone", spy)
    checkout(str(origin), tmp_path / "clone", timeout=7)
    assert seen == [7]
    assert socket.getdefaulttimeout() is None


def test_credentials_from_url_or_token_env():
    assert _split_credentials("git://d/campaigns", "tok") == ("git://d/campaigns", {})
    assert _split_credentials("https://gh/o/r.git", None) == ("https://gh/o/r.git", {})
    assert _split_credentials("https://gh/o/r.git", "tok") == (
        "https://gh/o/r.git",
        {"username": "x-access-token", "password": "tok"},
    )
    assert _split_credentials("https://u:p@gh:8443/o/r.git", "tok") == (
        "https://gh:8443/o/r.git",
        {"username": "u", "password": "p"},
    )


# -- T7 additions: shallow clone, update path, redaction, timeouts, token -----


def test_clone_and_update_ask_for_depth_one(origin, tmp_path, monkeypatch):
    """Both transports are asked for a shallow history (the campaigns repo
    only needs its tip); the local transport ignores depth, which is why
    this spies on the kwargs rather than counting commits."""
    seen = {}
    real_clone, real_fetch = porcelain.clone, porcelain.fetch

    def clone(*a, **kw):
        seen["clone"] = kw.get("depth")
        return real_clone(*a, **kw)

    def fetch(*a, **kw):
        seen["fetch"] = kw.get("depth")
        return real_fetch(*a, **kw)

    monkeypatch.setattr(porcelain, "clone", clone)
    monkeypatch.setattr(porcelain, "fetch", fetch)
    dest = checkout(str(origin), tmp_path / "clone")
    checkout(str(origin), dest)
    assert seen == {"clone": 1, "fetch": 1}


def test_update_does_not_reclone(origin, tmp_path, monkeypatch):
    dest = checkout(str(origin), tmp_path / "clone")
    marker = dest / ".git" / "marker"
    marker.write_text("kept")
    monkeypatch.setattr(
        porcelain, "clone", lambda *a, **kw: pytest.fail("update must fetch")
    )
    checkout(str(origin), dest)
    assert marker.read_text() == "kept"


def test_redact_strips_userinfo_only():
    assert _redact("clone https://u:s3cret@gh/o/r.git failed") == (
        "clone https://gh/o/r.git failed"
    )
    assert _redact("git://daemon/campaigns") == "git://daemon/campaigns"
    assert _redact("a@b outside a URL stays") == "a@b outside a URL stays"


def test_remote_head_prefers_head_then_main_then_master_then_first():
    assert _remote_head({b"HEAD": b"h", b"refs/heads/main": b"m"}) == b"h"
    assert _remote_head({b"refs/heads/main": b"m", b"refs/heads/master": b"x"}) == b"m"
    assert _remote_head({b"refs/heads/master": b"x", b"refs/heads/zeta": b"z"}) == b"x"
    assert _remote_head({b"refs/heads/zeta": b"z", b"refs/heads/alpha": b"a"}) == b"a"
    with pytest.raises(RuntimeError, match="no branch"):
        _remote_head({b"refs/tags/v1": b"t"})


def test_errors_name_the_operation(origin, tmp_path):
    with pytest.raises(RuntimeError, match=r"^git clone failed: "):
        checkout(str(tmp_path / "nowhere"), tmp_path / "c")
    dest = checkout(str(origin), tmp_path / "clone")
    with pytest.raises(RuntimeError, match=r"^git pull failed: "):
        checkout(str(tmp_path / "nowhere"), dest)


def test_socket_timeout_is_reported_as_a_timeout(tmp_path, monkeypatch):
    """dulwich surfaces a stalled transfer as socket.timeout (or an OSError
    saying so); the operator sees the budget, never the URL."""
    monkeypatch.setattr(
        porcelain, "clone", lambda *a, **kw: (_ for _ in ()).throw(socket.timeout())
    )
    with pytest.raises(RuntimeError, match=r"^git clone timed out after 7s$"):
        checkout("https://u:s3cret@host/x.git", tmp_path / "c", timeout=7)

    def stalled(*a, **kw):
        raise OSError("Connection to https://u:s3cret@host/x.git timed out")

    monkeypatch.setattr(porcelain, "clone", stalled)
    with pytest.raises(RuntimeError, match=r"^git clone timed out after 7s$"):
        checkout("https://u:s3cret@host/x.git", tmp_path / "c", timeout=7)


def test_socket_timeout_is_restored_after_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        porcelain, "clone", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with pytest.raises(RuntimeError):
        checkout("https://host/x.git", tmp_path / "c", timeout=3)
    assert socket.getdefaulttimeout() is None


def test_dulwich_exceptions_are_wrapped_with_their_type(tmp_path, monkeypatch):
    class HangupException(Exception):
        pass

    def hangup(*a, **kw):
        raise HangupException("remote https://u:s3cret@host/x.git hung up")

    monkeypatch.setattr(porcelain, "clone", hangup)
    with pytest.raises(RuntimeError) as e:
        checkout("https://u:s3cret@host/x.git", tmp_path / "c")
    assert (
        str(e.value)
        == "git clone failed: HangupException: remote https://host/x.git hung up"
    )


def test_git_token_env_reaches_dulwich_for_https_only(tmp_path, monkeypatch):
    calls = []

    def clone(url, dest, **kw):
        calls.append((url, kw))
        raise RuntimeError("stop here")

    monkeypatch.setattr(porcelain, "clone", clone)
    monkeypatch.setenv("GIT_TOKEN", "env-tok")
    with pytest.raises(RuntimeError):
        checkout("https://gh/o/r.git", tmp_path / "a")
    with pytest.raises(RuntimeError):
        checkout("https://gh/o/r.git", tmp_path / "b", token="arg-tok")
    with pytest.raises(RuntimeError):
        checkout("git://daemon/campaigns", tmp_path / "c")
    assert calls[0][1]["username"] == "x-access-token"
    assert calls[0][1]["password"] == "env-tok"
    assert calls[1][1]["password"] == "arg-tok"  # explicit argument wins
    assert "password" not in calls[2][1]  # git:// is unauthenticated
    assert all("depth" in kw for _, kw in calls)
