"""The session cookie as both pods handle it: its name, the origin the
browser sent a request to, and the rule for every HTTP client that carries
someone's cookie (docs: security, "Results"). Opening the cookie is
``session.SessionCodec``'s job, in the results proxy alone."""

from __future__ import annotations

from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Mapping

from starlette.requests import Request

#: The name pod-to-pod requests carry it under, and the browser's over
#: plain HTTP (development only).
COOKIE = "htr_session"
#: The browser's over HTTPS. A browser takes a `__Host-` cookie only from a
#: secure answer for this exact host (Path=/, no Domain), so a sibling
#: subdomain or a plain-HTTP answer cannot plant its own session here.
SECURE_COOKIE = f"__Host-{COOKIE}"


def forwarded(headers: Mapping[str, str], scheme: str) -> tuple[str, str]:
    """``(scheme, host)`` the browser used: ``X-Forwarded-Proto`` and
    ``-Host`` where an edge in front set them, each falling back to the
    request's own. The web front passes both on to the results proxy, so the
    two pods agree on the browser's origin."""
    return (
        headers.get("x-forwarded-proto") or scheme,
        headers.get("x-forwarded-host") or headers.get("host", ""),
    )


def browser_cookie(headers: Mapping[str, str], scheme: str) -> str:
    """The name the browser keeps the session under for this request."""
    secure = forwarded(headers, scheme)[0] == "https"
    return SECURE_COOKIE if secure else COOKIE


def session_token(request: Request) -> str | None:
    """The browser's session cookie, under that name only: a cookie by the
    other name is not a session."""
    return request.cookies.get(browser_cookie(request.headers, request.url.scheme))


def internal_cookie(token: str) -> dict[str, str]:
    """The header that carries a token from the web front to the results
    proxy on the web front's own behalf (the session check, the progress
    reads). Such a request has no forwarded headers, so the proxy takes it
    as plain HTTP and reads the plain name."""
    return {"Cookie": f"{COOKIE}={token}"}


def no_cookie_jar() -> CookieJar:
    """For every client this pod shares between people when it talks to the
    results proxy. httpx keeps each Set-Cookie in the client's own jar and
    adds it to any later request that has no Cookie header of its own, so a
    shared client with an ordinary jar would carry one person's session on
    the next person's request. This jar stores nothing."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
