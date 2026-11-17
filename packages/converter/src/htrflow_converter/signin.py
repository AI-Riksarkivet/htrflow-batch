"""The device sign-in (RFC 8628) against the provider the server names. The
codes and tokens go only to addresses that passed `checked_address`, and no
error text holds a token."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from .remote import (
    Login,
    RemoteError,
    _body,
    _lifetime,
    _version,
    checked_address,
)

_SERVICE = "the sign-in service"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_CODE = re.compile(r"[a-z_]{1,40}")
_USER_CODE = re.compile(r"[A-Za-z0-9 _-]{1,64}")
_INTERVAL = 5.0
_MAX_INTERVAL = 60.0
_MAX_LIFETIME = 3600.0
_MAX_FAILURES = 3  # network failures in a row that end the wait
_TRY = "Try again; if it stays, tell whoever runs the server."


def _numbers(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")[:3]
    if len(parts) < 3 or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def too_old(client: str, minimum: str) -> bool:
    """Whether ``client`` is a readable version below ``minimum``; a version
    that does not start with three numbers is not too old."""
    have, need = _numbers(client), _numbers(minimum)
    return have is not None and need is not None and have < need


def _call(client: httpx.Client, host: str, method: str, url: str, **kw: Any):
    try:
        response = client.request(method, url, **kw)
    except httpx.HTTPError:
        raise RemoteError(
            f"Could not reach {host}.",
            "Check the address and your network, then try again.",
        ) from None
    if 300 <= response.status_code < 400:
        raise RemoteError(
            f"{host} answered with a redirect, which is not followed.",
            "Check the address.",
            response.status_code,
        )
    return response


def _object(response: httpx.Response, what: str) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not response.is_success or not isinstance(body, dict):
        detail, hint = _body(response) if not response.is_success else ("", "")
        raise RemoteError(
            detail or f"{what} answered with something this CLI cannot read.",
            hint or _TRY,
            response.status_code,
        )
    return body


def _text(body: dict, key: str, what: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise RemoteError(f"{what} did not give a usable {key}.", _TRY)
    return value


def _printable(text: str) -> bool:
    return text.isprintable() and not any(c.isspace() for c in text)


def _shown(url: str, what: str, complete: bool = False) -> str:
    """A verification address that passed the address rule. The "complete"
    one carries the user code as a query, so the rule is applied to the
    address before it and the whole is shown."""
    if not _printable(url):
        raise RemoteError(f"The address of {what} is not acceptable.", _TRY)
    base = url.split("?", 1)[0] if complete else url
    checked_address(base, what)
    return url


def _refusal(body: dict) -> RemoteError:
    code = body.get("error")
    named = f" ({code})" if isinstance(code, str) and _CODE.fullmatch(code) else ""
    return RemoteError(f"The sign-in service refused the sign-in{named}.", _TRY)


def _positive(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value) if 0 < value < float("inf") else default


def device_login(
    server: str,
    client: httpx.Client,
    say: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> Login:
    server = checked_address(server, "the server")
    info = _object(_call(client, server, "GET", f"{server}/v1/login"), "the server")
    minimum = info.get("minClient")
    if isinstance(minimum, str) and too_old(_version(), minimum):
        raise RemoteError(
            f"This client ({_version()}) is older than the server's minimum "
            f"({minimum}).",
            "Upgrade htrflow-campaigns, then sign in again.",
        )
    given = _text(info, "issuer", "the server")
    issuer = checked_address(given, _SERVICE)
    client_id = _text(info, "clientId", "the server")

    meta = _object(
        _call(
            client,
            issuer,
            "GET",
            f"{issuer}/.well-known/openid-configuration",
        ),
        _SERVICE,
    )
    if str(meta.get("issuer", "")).removesuffix("/") != given.removesuffix("/"):
        raise RemoteError(
            f"The sign-in service names itself {meta.get('issuer')!s:.200}, "
            f"but the server names {given}.",
            "Tell whoever runs the server.",
        )
    if not all(isinstance(meta.get(k), str) and meta[k] for k in _ENDPOINTS):
        raise RemoteError(
            "The sign-in service does not offer the device sign-in.",
            "Tell whoever runs the server.",
        )
    device_endpoint = checked_address(meta[_ENDPOINTS[0]], _SERVICE)
    token_endpoint = checked_address(meta[_ENDPOINTS[1]], _SERVICE)

    device = _object(
        _call(
            client,
            device_endpoint,
            "POST",
            device_endpoint,
            data={"client_id": client_id, "scope": "openid"},
        ),
        _SERVICE,
    )
    device_code = _text(device, "device_code", _SERVICE)
    user_code = _text(device, "user_code", _SERVICE)
    if not _USER_CODE.fullmatch(user_code):
        raise RemoteError("The sign-in service gave a code that cannot be shown.", _TRY)
    uri = _shown(_text(device, "verification_uri", _SERVICE), _SERVICE)
    complete = device.get("verification_uri_complete")
    if isinstance(complete, str) and complete:
        say(f"Open {_shown(complete, _SERVICE, complete=True)} to sign in.")
        say(f"The code is {user_code}.")
    else:
        say(f"Open {uri} and enter the code {user_code}.")
    interval = min(_positive(device.get("interval"), _INTERVAL), _MAX_INTERVAL)
    lifetime = min(_positive(device.get("expires_in"), 600.0), _MAX_LIFETIME)
    deadline = now() + lifetime

    failures = 0
    while now() < deadline:
        sleep(interval)
        try:
            response = _call(
                client,
                token_endpoint,
                "POST",
                token_endpoint,
                data={
                    "grant_type": _DEVICE_GRANT,
                    "device_code": device_code,
                    "client_id": client_id,
                },
            )
        except RemoteError:
            failures += 1
            if failures >= _MAX_FAILURES:
                raise RemoteError(
                    f"Could not reach {token_endpoint} while waiting.",
                    "Check your network, then sign in again.",
                ) from None
            continue
        failures = 0
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise RemoteError(
                "The sign-in service answered with something this CLI cannot read.",
                _TRY,
            )
        token = body.get("access_token")
        if response.is_success and isinstance(token, str) and token:
            refresh = body.get("refresh_token") or ""
            if not isinstance(refresh, str):
                raise _refusal({})
            return Login(
                server=server,
                issuer=issuer,
                client_id=client_id,
                token_endpoint=token_endpoint,
                access_token=token,
                refresh_token=refresh,
                expires_at=now() + _lifetime(body.get("expires_in", 3600)),
            )
        code = body.get("error")
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval = min(interval + 5, _MAX_INTERVAL)
            continue
        if code == "access_denied":
            raise RemoteError(
                "The sign-in was denied.", "Sign in again if that was a mistake."
            )
        if code == "expired_token":
            break
        raise _refusal(body)
    raise RemoteError(
        "The sign-in code expired before it was used.",
        "Sign in again: htrflow-campaigns login",
    )


_ENDPOINTS = ("device_authorization_endpoint", "token_endpoint")
