"""Whether a request is logged in -- asked of the results proxy, which alone
can open the cookie (docs: security, "Results"). Cached per cookie value."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

COOKIE = "htr_session"
MAX_COOKIE = 4096


def no_cookie_jar() -> CookieJar:
    """For every client this pod shares between people when it talks to the
    results proxy. httpx keeps each Set-Cookie in the client's own jar and
    adds it to any later request that has no Cookie header of its own, so a
    shared client with an ordinary jar would carry one person's session on
    the next person's request. This jar stores nothing."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


class SessionsUnavailable(Exception):
    """The results proxy did not answer the session check."""


@dataclass(frozen=True)
class Session:
    user: str
    cookie: str


class SessionChecker:
    def __init__(
        self,
        proxy_base: str,
        client: httpx.Client | None = None,
        ttl: float = 30.0,
        maxsize: int = 1024,
        clock=time.monotonic,
    ) -> None:
        self._url = f"{proxy_base.rstrip('/')}/_session"
        self._client = client or httpx.Client(timeout=3.0, cookies=no_cookie_jar())
        self._ttl, self._max, self._clock = ttl, maxsize, clock
        self._cache: OrderedDict[str, tuple[float, Session | None]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, cookie: str | None) -> Session | None:
        # A sealed session is a few hundred ASCII bytes. Anything else is no
        # session, answered before the proxy is asked or a cache key is kept
        # (a header value that is not ASCII could not be forwarded at all).
        if not cookie or len(cookie) > MAX_COOKIE or not cookie.isascii():
            return None
        now = self._clock()
        with self._lock:
            hit = self._cache.get(cookie)
        if hit and hit[0] > now:
            return hit[1]
        try:
            r = self._client.get(self._url, headers={"Cookie": f"{COOKIE}={cookie}"})
        except httpx.HTTPError as e:
            raise SessionsUnavailable(str(e)) from e
        if r.status_code == 200:
            try:
                found: Session | None = Session(str(r.json()["user"]), cookie)
            except (ValueError, KeyError, TypeError) as e:
                raise SessionsUnavailable("session check answered no user") from e
        elif r.status_code == 401:
            found = None
        else:
            raise SessionsUnavailable(f"session check answered {r.status_code}")
        with self._lock:
            self._cache[cookie] = (now + self._ttl, found)
            self._cache.move_to_end(cookie)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return found
