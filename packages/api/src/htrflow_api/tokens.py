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
#: How far the sign-in service's clock may be from this pod's, for ``exp``
#: and ``nbf``. ``iat`` is not checked: a token issued "in the future" by a
#: clock slightly ahead is still the provider's token.
LEEWAY_SECONDS = 30
FETCH_SECONDS = 5.0

_RE_LOGIN = "Sign in again: htrflow-campaigns login"
_GROUP_HINT = (
    "Ask whoever runs the sign-in service to add you to the group, and to send "
    'group names in the token\'s groups claim with "full group path" off.'
)
_NOT_YET = (
    "This token is not valid yet: the sign-in service's clock is ahead of "
    "this service's.",
    "Tell whoever runs the cluster; signing in again will not help.",
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
        self._http = http  # built on first use, so a supplied fetch needs none
        self._lock = threading.Lock()  # guards reloads only, never a lookup
        self._keys: dict[str, object] = {}  # replaced whole, never edited
        self._last: float | None = None  # when the keys were last asked for
        self._down = False  # whether that last ask failed

    def _get_json(self, url: str, deadline: float) -> dict:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Unreachable
        if self._http is None:
            self._http = httpx.Client()
        resp = self._http.get(url, timeout=left)
        doc = resp.json() if resp.status_code == 200 else None
        if not isinstance(doc, dict):
            raise _Unreachable
        return doc

    def _fetch_over_http(self) -> dict:
        # One 5 s budget for discovery and keys together: each request gets
        # what is left of it as its timeout, and none starts once it is spent.
        deadline = time.monotonic() + FETCH_SECONDS
        url = self._cfg.jwks_url
        if not url:
            base = self._cfg.issuer.rstrip("/")
            found = self._get_json(f"{base}/.well-known/openid-configuration", deadline)
            url = found.get("jwks_uri")
            if not isinstance(url, str) or not url:
                raise _Unreachable
        return self._get_json(url, deadline)

    def _reload(self) -> None:
        """Ask the provider for its keys (lock held). Any failure to fetch or
        read the answer means the provider cannot be reached."""
        self._last = self._clock()
        try:
            entries = self._fetch()["keys"]
            if not isinstance(entries, list):
                raise _Unreachable
        except Exception:  # noqa: BLE001 - whatever it was, the keys are not there
            self._down = True
            return
        keys: dict[str, object] = {}
        for entry in entries:
            try:
                keys[entry["kid"]] = jwt.PyJWK.from_dict(entry).key
            except Exception:  # noqa: BLE001 - one bad entry is not fatal
                continue
        self._keys, self._down = keys, False

    def _key_for(self, kid: str):
        key = self._keys.get(kid)  # no lock: a cached key never waits on a fetch
        if key is not None:
            return key
        with self._lock:
            key = self._keys.get(kid)
            if key is not None:
                return key
            due = self._last is None or self._clock() - self._last >= REFETCH_SECONDS
            if due:
                self._reload()
                key = self._keys.get(kid)
                if key is not None:
                    return key
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
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud"], "verify_iat": False},
            )
        except jwt.ExpiredSignatureError:
            raise _unauthorized("The token has expired.") from None
        except jwt.ImmatureSignatureError:
            raise Refused(401, *_NOT_YET) from None
        except jwt.InvalidIssuerError:
            raise _unauthorized(
                f"This token comes from another issuer than {cfg.issuer}."
            ) from None
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
            raise Refused(
                401,
                f"The token has no {cfg.user_claim} to act as.",
                "Ask whoever runs the sign-in service to send the user name in "
                f"the token's {cfg.user_claim} claim.",
            )
        groups = claims.get(cfg.groups_claim)
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            groups = []
        if cfg.group not in groups:
            raise Refused(403, f"You are not in the group {cfg.group}.", _GROUP_HINT)
        return Identity(user, tuple(groups))
