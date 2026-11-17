"""Turns a signed OpenID Connect access token into the caller's user name and
groups, or refuses it. The only state is the provider's signing keys, in
memory; the token is never logged, quoted or sent anywhere."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import jwt

from htrflow_api.config import ApiConfig

#: Asymmetric only: never "none", never an HS* (the key is public).
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
              "ES256", "ES384", "ES512", "EdDSA")  # fmt: skip
REFETCH_SECONDS = 60

_RE_LOGIN = "Sign in again: htrflow-campaigns login"
_GROUP_HINT = (
    "Ask whoever runs the sign-in service to add you to the group, and to send "
    'group names in the token\'s groups claim with "full group path" off.'
)
_DOWN_DETAIL = "The sign-in service could not be reached to check this token."
_DOWN_HINT = "Try again in a minute; if it stays, tell whoever runs the cluster."


@dataclass(frozen=True)
class Identity:
    user: str
    groups: tuple[str, ...]


class Refused(Exception):
    def __init__(self, status: int, detail: str, hint: str) -> None:
        super().__init__(detail)
        self.status, self.detail, self.hint = status, detail, hint


def _unauthorized(detail: str) -> Refused:
    return Refused(401, detail, _RE_LOGIN)


class _Unreachable(Exception):
    pass


class Verifier:
    def __init__(
        self,
        cfg: ApiConfig,
        fetch_keys: Callable[[], dict] | None = None,
        clock: Callable[[], float] = time.monotonic,
        http: httpx.Client | None = None,
    ) -> None:
        self._cfg = cfg
        self._fetch = fetch_keys or self._fetch_over_http
        self._clock = clock
        self._http = http or httpx.Client(timeout=5.0)
        self._lock = threading.Lock()
        self._keys: dict[str, object] = {}
        self._last: float | None = None  # when the keys were last asked for
        self._down = False  # whether that last ask failed

    def _get_json(self, url: str) -> dict:
        try:
            resp = self._http.get(url)
            doc = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            raise _Unreachable from e
        if not isinstance(doc, dict):
            raise _Unreachable
        return doc

    def _fetch_over_http(self) -> dict:
        url = self._cfg.jwks_url
        if not url:
            base = self._cfg.issuer.rstrip("/")
            url = self._get_json(f"{base}/.well-known/openid-configuration").get(
                "jwks_uri"
            )
            if not isinstance(url, str) or not url:
                raise _Unreachable
        return self._get_json(url)

    def _reload(self) -> None:
        """Ask the provider for its keys (lock held)."""
        self._last = self._clock()
        try:
            doc = self._fetch()
            entries = doc["keys"]
            if not isinstance(entries, list):
                raise _Unreachable
        except (_Unreachable, httpx.HTTPError, KeyError, TypeError, ValueError):
            self._down = True
            return
        keys: dict[str, object] = {}
        for entry in entries:
            try:
                keys[entry["kid"]] = jwt.PyJWK.from_dict(entry).key
            except (jwt.PyJWTError, KeyError, TypeError, ValueError):
                continue  # a key this library cannot load is not fatal
        self._keys, self._down = keys, False

    def _key_for(self, kid: str | None):
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            due = self._last is None or self._clock() - self._last >= REFETCH_SECONDS
            if due:
                self._reload()
                if kid in self._keys:
                    return self._keys[kid]
            if self._down:
                raise Refused(503, _DOWN_DETAIL, _DOWN_HINT)
        raise _unauthorized(
            "The token was signed with a key this service does not know."
        )

    def verify(self, token: str) -> Identity:
        try:
            kid = jwt.get_unverified_header(token).get("kid")  # only kid is used
        except (jwt.PyJWTError, ValueError, TypeError):
            raise _unauthorized("This is not a sign-in token.") from None
        if not isinstance(kid, str):
            raise _unauthorized("The token names no signing key.")
        key = self._key_for(kid)
        cfg = self._cfg
        try:
            claims = jwt.decode(
                token,
                key,  # type: ignore[arg-type]
                algorithms=list(ALGORITHMS),
                audience=cfg.audience,
                issuer=cfg.issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError:
            raise _unauthorized("The token has expired.") from None
        except jwt.InvalidIssuerError:
            raise _unauthorized("The token comes from another issuer.") from None
        except (jwt.InvalidAudienceError, jwt.MissingRequiredClaimError) as e:
            what = getattr(e, "claim", "aud")
            detail = {
                "exp": "The token has no expiry.",
                "iss": "The token names no issuer.",
            }.get(
                what,
                f"The token is not for this service; it must be for {cfg.audience}.",
            )
            raise _unauthorized(detail) from None
        except (jwt.PyJWTError, ValueError, TypeError):
            raise _unauthorized(
                "The token's signature or contents are not valid."
            ) from None
        user = claims.get(cfg.user_claim)
        if not isinstance(user, str) or not user:
            raise _unauthorized(f"The token has no {cfg.user_claim} to act as.")
        groups = claims.get(cfg.groups_claim)
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            groups = []
        if cfg.group not in groups:
            raise Refused(403, f"You are not in the group {cfg.group}.", _GROUP_HINT)
        return Identity(user, tuple(groups))
