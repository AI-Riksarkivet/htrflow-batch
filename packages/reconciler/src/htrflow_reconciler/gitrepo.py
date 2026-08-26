"""Shallow clone / update of the campaigns repo with dulwich — no ``git``
binary, no subprocess, so the reconciler image carries no shell tool that
campaign data could ever reach (audit S1/S7).

Errors never carry the repo URL: a campaigns URL may embed a token and this
runs in a CronJob whose stderr lands in cluster logs. Anything dulwich says is
passed through minus any ``user:token@`` userinfo, so failures stay
diagnosable without leaking the credential.

Transports: ``git://`` (unauthenticated, the PoC's in-cluster daemon),
``https://`` (token via URL userinfo or ``GIT_TOKEN``), and local paths /
``file://`` for tests. Both network transports support ``depth=1``; the
local transport clones full (dulwich's local client ignores depth) which
only matters for tests.

The socket timeout is set process-wide for the duration of the call: dulwich
reaches ``git://`` through plain sockets and ``https://`` through urllib3,
and both honour the default socket timeout. The value must stay at or below
the CronJob's activeDeadlineSeconds (audit O7), which is what bounds a
trickling transfer overall.
"""

from __future__ import annotations

import os
import re
import socket
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dulwich import porcelain
from dulwich.objects import ObjectID
from dulwich.refs import HEADREF
from dulwich.repo import Repo

_USERINFO = re.compile(r"(://)[^/\s@]*@")

#: Transfer progress is noise in CronJob logs; failures raise.
_QUIET = porcelain.NoneStream()

#: Default git operation timeout; ``__main__`` clamps it to the tick deadline.
DEFAULT_TIMEOUT = 300


def _redact(text: str) -> str:
    return _USERINFO.sub(r"\1", text)


def _split_credentials(url: str, token: str | None) -> tuple[str, dict]:
    """(URL without userinfo, dulwich auth kwargs).

    A token in the URL (``https://user:token@host/…``) wins; otherwise
    ``GIT_TOKEN`` is sent as a password with the GitHub-style
    ``x-access-token`` user, which every major forge accepts for PATs.
    """
    u = urlsplit(url)
    if u.scheme not in ("http", "https"):
        return url, {}
    if u.username or u.password:
        host = u.hostname or ""
        if u.port:
            host = f"{host}:{u.port}"
        clean = urlunsplit((u.scheme, host, u.path, u.query, u.fragment))
        return clean, {"username": u.username or "", "password": u.password or ""}
    if token:
        return url, {"username": "x-access-token", "password": token}
    return url, {}


@contextmanager
def _socket_timeout(seconds: int):
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)


def _remote_head(refs: dict) -> ObjectID:
    """The commit to check out after a fetch: the remote's HEAD, else main or
    master (the daemon may not advertise a symbolic HEAD)."""
    for name in (b"HEAD", b"refs/heads/main", b"refs/heads/master"):
        sha = refs.get(name)
        if sha:
            return ObjectID(sha)
    heads = sorted(k for k in refs if k.startswith(b"refs/heads/"))
    if heads:
        return ObjectID(refs[heads[0]])
    raise RuntimeError("remote advertises no branch")


def _update(dest: Path, url: str, auth: dict) -> None:
    """fetch + hard reset to the remote head — never a merge, so a rewritten
    history on the campaigns repo (force-push) cannot wedge the checkout."""
    repo = Repo(str(dest))
    try:
        result = porcelain.fetch(repo, url, depth=1, errstream=_QUIET, **auth)
        sha = _remote_head(dict(result.refs))
        refnames, _ = repo.refs.follow(HEADREF)
        repo.refs[refnames[-1]] = sha  # the branch HEAD points at
        porcelain.reset(repo, "hard", sha)
    finally:
        repo.close()


def checkout(
    url: str,
    dest: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    token: str | None = None,
) -> Path:
    """Clone ``url`` shallowly into ``dest``, or bring an existing clone up to
    the remote head. ``token`` defaults to ``GIT_TOKEN``."""
    dest = Path(dest)
    clean, auth = _split_credentials(url, token or os.environ.get("GIT_TOKEN"))
    op = "pull" if (dest / ".git").exists() else "clone"
    try:
        with _socket_timeout(timeout):
            if op == "pull":
                _update(dest, clean, auth)
            else:
                porcelain.clone(
                    clean, str(dest), depth=1, errstream=_QUIET, **auth
                ).close()
    except (OSError, socket.timeout) as e:
        if isinstance(e, socket.timeout) or "timed out" in str(e).lower():
            raise RuntimeError(f"git {op} timed out after {timeout}s") from None
        raise RuntimeError(f"git {op} failed: {_redact(str(e))}") from None
    except Exception as e:  # noqa: BLE001 — dulwich's own exception zoo
        raise RuntimeError(
            f"git {op} failed: {type(e).__name__}: {_redact(str(e))}"
        ) from None
    return dest
