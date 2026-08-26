"""The campaigns checkout without a git binary: dulwich against a local
repository fixture (built with dulwich too, so the suite needs no git)."""

import socket

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from htrflow_reconciler.gitrepo import _split_credentials, checkout


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
