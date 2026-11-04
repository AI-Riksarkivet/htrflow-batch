"""HTTP reads for `export`: the results proxy (cookie session) and IIIF sizes."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx


class SessionLost(RuntimeError):
    """Login refused, or the session could not be renewed."""


class Forbidden(RuntimeError):
    """The account may not read this object."""


class ResultsUnavailable(RuntimeError):
    """The proxy answered something other than a file, a 404, 401 or 403."""


class SizeUnreadable(RuntimeError):
    """No usable width/height from the image server."""


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else response.reason_phrase


class Results:
    def __init__(
        self, results_url: str, user: str, password: str, client: httpx.Client
    ) -> None:
        self._base = results_url.rstrip("/")
        parts = urlsplit(self._base)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._user = user
        self._password = password
        self._client = client

    def login(self) -> None:
        response = self._client.post(
            f"{self._base}/_login",
            data={"username": self._user, "password": self._password},
            headers={"Origin": self._origin},
        )
        if response.status_code != 204:
            raise SessionLost(
                f"login refused ({response.status_code}): {_detail(response)}"
            )

    def get(self, key: str) -> bytes | None:
        response = self._client.get(f"{self._base}/{key}")
        if response.status_code == 401:
            self.login()
            response = self._client.get(f"{self._base}/{key}")
            if response.status_code == 401:
                raise SessionLost(f"session rejected again: {_detail(response)}")
        match response.status_code:
            case 200:
                return response.content
            case 404:
                return None
            case 403:
                raise Forbidden(_detail(response))
            case status:
                raise ResultsUnavailable(f"{status}: {_detail(response)}")


def image_size(client: httpx.Client, url: str) -> tuple[int, int]:
    try:
        response = client.get(url)
        response.raise_for_status()
        info = response.json()
        width, height = info["width"], info["height"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SizeUnreadable(f"{url}: {exc}") from exc
    if not all(
        isinstance(n, int) and not isinstance(n, bool) and n > 0
        for n in (width, height)
    ):
        raise SizeUnreadable(f"{url}: width/height not positive integers")
    return width, height
