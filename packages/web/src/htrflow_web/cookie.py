"""The session cookie as both pods handle it: its name, the origin the
browser sent a request to, and the rule for every HTTP client that carries
someone's cookie (docs: security, "Results"). Opening the cookie is
``session.SessionCodec``'s job, in the results proxy alone."""

from __future__ import annotations

from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Mapping

COOKIE = "htr_session"


def forwarded(headers: Mapping[str, str], scheme: str) -> tuple[str, str]:
    """``(scheme, host)`` the browser used: ``X-Forwarded-Proto`` and
    ``-Host`` where an edge in front set them, each falling back to the
    request's own. The web front passes both on to the results proxy, so the
    two pods agree on the browser's origin."""
    return (
        headers.get("x-forwarded-proto") or scheme,
        headers.get("x-forwarded-host") or headers.get("host", ""),
    )


def no_cookie_jar() -> CookieJar:
    """For every client this pod shares between people when it talks to the
    results proxy. httpx keeps each Set-Cookie in the client's own jar and
    adds it to any later request that has no Cookie header of its own, so a
    shared client with an ordinary jar would carry one person's session on
    the next person's request. This jar stores nothing."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
