"""The results proxy's session: the logged-in user's store keys, sealed into
the ``htr_session`` cookie with AES-GCM. Never the password; never readable
by JavaScript (the cookie is HttpOnly) or by the web front (it has no key).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

COOKIE = "htr_session"
_NONCE = 12
_ASSOCIATED_DATA = b"htr_session"


@dataclass(frozen=True)
class SessionData:
    user: str
    access_key: str
    secret_key: str
    expires: float


def load_key(path: str) -> bytes:
    with open(path, encoding="ascii") as f:
        key = base64.b64decode(f.read().strip())
    if len(key) != 32:
        raise ValueError(f"{path}: the session key must be 32 bytes, got {len(key)}")
    return key


def derive_keys(
    username: str, password: str, mode: Literal["hcp", "none"]
) -> tuple[str, str]:
    """HCP's S3 keys are derived from the account: base64 of the user name,
    hex MD5 of the password. A store that issues keys takes them as given."""
    if mode == "none":
        return username, password
    access = base64.b64encode(username.encode()).decode()
    secret = hashlib.md5(password.encode(), usedforsecurity=False).hexdigest()
    return access, secret


class SessionCodec:
    def __init__(
        self, key: bytes, hours: float, clock: Callable[[], float] = time.time
    ) -> None:
        self._aead = AESGCM(key)
        self._ttl = hours * 3600
        self._clock = clock

    def seal(self, user: str, access_key: str, secret_key: str) -> str:
        body = json.dumps(
            {
                "u": user,
                "a": access_key,
                "s": secret_key,
                "e": self._clock() + self._ttl,
            }
        ).encode()
        nonce = os.urandom(_NONCE)
        sealed = nonce + self._aead.encrypt(nonce, body, _ASSOCIATED_DATA)
        return base64.urlsafe_b64encode(sealed).decode().rstrip("=")

    def open(self, token: str) -> SessionData | None:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            body = self._aead.decrypt(raw[:_NONCE], raw[_NONCE:], _ASSOCIATED_DATA)
            d = json.loads(body)
            data = SessionData(d["u"], d["a"], d["s"], float(d["e"]))
        except (binascii.Error, ValueError, InvalidTag, KeyError, TypeError):
            return None
        return data if data.expires > self._clock() else None
