"""The CLI's side of the campaigns API: the address rule, the stored login,
and requests that carry the token. The token goes to the service and the
refresh token to the sign-in service's token endpoint, nowhere else, and no
error text holds either."""

from __future__ import annotations

import importlib.metadata
import ipaddress
import json as jsonlib
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ENV_URL = "HTRFLOW_API_URL"
CLIENT_HEADER = "X-Htrflow-Client"
_RE_LOGIN = "Sign in again: htrflow-campaigns login"
_SKEW = 30.0  # a token this close to expiry is refreshed first
_TIMEOUT = 30.0


class RemoteError(Exception):
    def __init__(self, detail: str, hint: str = "", status: int | None = None) -> None:
        super().__init__(f"{detail} {hint}".strip())
        self.detail, self.hint, self.status = detail, hint, status


class NotSignedIn(RemoteError):
    def __init__(
        self,
        detail: str = "You are not signed in.",
        hint: str = _RE_LOGIN,
        status: int | None = None,
    ) -> None:
        super().__init__(detail, hint, status)


@dataclass(frozen=True)
class Login:
    server: str  # normalised: no trailing slash
    issuer: str
    client_id: str
    token_endpoint: str
    access_token: str
    refresh_token: str  # "" when the provider gave none
    expires_at: float  # seconds since the epoch


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in ipaddress.ip_network("127.0.0.0/8")
    return ip == ipaddress.IPv6Address("::1")


def checked_address(url: str, what: str) -> str:
    """The address without a trailing slash, or a RemoteError naming ``what``.
    https, or http for a loopback host; no user info, query or fragment."""
    bad = RemoteError(
        f"The address of {what} is not acceptable.",
        "Use an https:// address (http:// only for localhost) with no user name, "
        "query or fragment.",
    )
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise bad from None
    if (
        not host
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or "?" in url
        or "#" in url
        or not (parts.scheme == "https" or (parts.scheme == "http" and _loopback(host)))
    ):
        raise bad
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def login_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("XDG_CONFIG_HOME"):
        base = Path(env["XDG_CONFIG_HOME"])
    else:
        base = (Path(env["HOME"]) if env.get("HOME") else Path.home()) / ".config"
    return base / "htrflow" / "login.json"


def _private(st: os.stat_result) -> bool:
    return not st.st_mode & 0o077 and st.st_uid == os.getuid()


def _read_private(path: Path) -> str:
    """The file's text, once the open file and its directory are known to be
    the user's alone; the check is on the descriptor that is then read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        if not (_private(os.fstat(f.fileno())) and _private(path.parent.stat())):
            raise NotSignedIn(
                "The stored sign-in could be read by others, so it is not used.",
                _RE_LOGIN,
            )
        return f.read()


def load(env: Mapping[str, str] | None = None) -> Login:
    try:
        raw = jsonlib.loads(_read_private(login_path(env)))
        login = Login(**{k: raw[k] for k in Login.__dataclass_fields__})
        strings = [v for k, v in asdict(login).items() if k != "expires_at"]
        if (
            not all(isinstance(v, str) for v in strings)
            or isinstance(login.expires_at, bool)
            or not isinstance(login.expires_at, int | float)
        ):
            raise ValueError
        server = checked_address(login.server, "the server")
    except NotSignedIn:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RemoteError):
        raise NotSignedIn(
            "No usable sign-in is stored on this machine.", _RE_LOGIN
        ) from None
    return replace(login, server=server)


def save(login: Login, env: Mapping[str, str] | None = None) -> None:
    path = login_path(env)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(jsonlib.dumps(asdict(login)))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def forget(env: Mapping[str, str] | None = None) -> bool:
    try:
        login_path(env).unlink()
    except FileNotFoundError:
        return False
    return True


def _body(response: httpx.Response) -> tuple[str, str]:
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            hint = body.get("hint")
            return body["detail"], hint if isinstance(hint, str) else ""
    except ValueError:
        pass
    return f"the server answered {response.status_code}", ""


def _lifetime(value: Any) -> float:
    """Seconds a new token lasts; anything unusable is a minute, so the next
    call refreshes again rather than never."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 60.0
    return seconds if math.isfinite(seconds) and seconds > 0 else 60.0


def _version() -> str:
    try:
        return importlib.metadata.version("htrflow-converter")
    except importlib.metadata.PackageNotFoundError:
        return ""


class Remote:
    def __init__(
        self,
        login: Login,
        client: httpx.Client,
        env: Mapping[str, str] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.login, self._client, self._env, self._now = login, client, env, now

    def _send(self, host: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One request; a failure to reach the host names the host only."""
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise RemoteError(
                f"Could not reach {host}.",
                "Check the address and your network, then try again.",
            ) from None
        if response.is_redirect or 300 <= response.status_code < 400:
            raise RemoteError(
                "the server answered with a redirect, which is not followed.",
                "Check the server address.",
                response.status_code,
            )
        return response

    def _refresh(self) -> None:
        login = self.login
        endpoint = checked_address(login.token_endpoint, "the sign-in service")
        if not login.refresh_token:
            raise NotSignedIn("Your sign-in has expired.", _RE_LOGIN)
        response = self._send(
            endpoint,
            "POST",
            endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": login.refresh_token,
                "client_id": login.client_id,
            },
        )
        try:
            body = response.json()
            token = body["access_token"]
            expires = _lifetime(body.get("expires_in", 3600))
            new_refresh = body.get("refresh_token") or login.refresh_token
            if (
                response.status_code != 200
                or not isinstance(token, str)
                or not isinstance(new_refresh, str)
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise NotSignedIn(
                "The sign-in service refused to renew your sign-in.", _RE_LOGIN
            ) from None
        self.login = replace(
            login,
            access_token=token,
            refresh_token=new_refresh,
            expires_at=self._now() + expires,
        )
        try:
            save(self.login, self._env)
        except OSError:
            pass  # this call goes ahead; the next one refreshes again

    def request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if not path.startswith("/"):
            raise ValueError(f"a request path starts with '/', not {path!r}")
        if self.login.expires_at - self._now() < _SKEW:
            self._refresh()
        response = self._send(
            self.login.server,
            method,
            self.login.server + path,
            json=json,
            params=params,
            headers={
                "Authorization": f"Bearer {self.login.access_token}",
                CLIENT_HEADER: _version(),
            },
        )
        if response.is_success:
            return response
        detail, hint = _body(response)
        if response.status_code == 401:
            raise NotSignedIn(detail, hint or _RE_LOGIN, 401)
        raise RemoteError(detail, hint, response.status_code)

    def get(self, path: str, **params: str) -> dict:
        response = self.request("GET", path, params=params or None)
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise RemoteError(
                "the server answered with something this CLI cannot read.",
                "Upgrade htrflow-campaigns, then try again.",
            )
        return body


def remote(env: Mapping[str, str] | None = None) -> Remote:
    env = os.environ if env is None else env
    login = load(env)
    wanted = env.get(ENV_URL)
    if wanted and checked_address(wanted, "the server") != login.server:
        raise NotSignedIn(
            f"{ENV_URL} is {wanted} but you are signed in to {login.server}.",
            _RE_LOGIN,
        )
    # Plain http (loopback only) is never sent through a proxy named in the
    # environment: a token would leave the machine in the clear. https keeps
    # the environment's proxy and CA settings.
    client = httpx.Client(
        timeout=_TIMEOUT,
        follow_redirects=False,
        mounts={"http://": httpx.HTTPTransport()},
    )
    return Remote(login, client, env)
