"""The results proxy's session: the logged-in user's store keys, sealed into
the session cookie (``cookie.COOKIE``) with AES-GCM. Never the password; never readable
by JavaScript (the cookie is HttpOnly) or by the web front (it has no key).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LOG = logging.getLogger("htrflow_web.session")
_NONCE = 12
#: Binds a sealed session to this use. Not the cookie's name, which may
#: change without logging anyone out; changing this would.
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


class SessionKeyUnavailable(Exception):
    """The mounted session key file is missing or not a key."""


class KeyFileCodec:
    """A ``SessionCodec`` over the mounted key file, re-read whenever the
    file changes: rotating the Secret ends every session sealed under the
    old key without restarting the pod. An unchanged file costs a stat per
    call. A file that turns unreadable opens no session and seals none
    until a good key is back: a rotation meant to end sessions must never
    leave the old key in use. Unreadable at start, it stops the start."""

    def __init__(
        self, path: str, hours: float, clock: Callable[[], float] = time.time
    ) -> None:
        self._path, self._hours, self._clock = path, hours, clock
        self._lock = threading.Lock()
        self._seen = self._stat()
        self._codec: SessionCodec | None = SessionCodec(load_key(path), hours, clock)

    def _stat(self) -> tuple[int, int, int] | None:
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size

    def _current(self) -> SessionCodec | None:
        seen = self._stat()
        with self._lock:
            if seen != self._seen:
                self._seen = seen
                try:
                    self._codec = SessionCodec(
                        load_key(self._path), self._hours, self._clock
                    )
                    _LOG.info("session key changed: earlier sessions have ended")
                except (OSError, ValueError) as e:
                    self._codec = None
                    _LOG.error("session key unreadable, no session opens: %s", e)
            return self._codec

    def seal(self, user: str, access_key: str, secret_key: str) -> str:
        codec = self._current()
        if codec is None:
            raise SessionKeyUnavailable(self._path)
        return codec.seal(user, access_key, secret_key)

    def open(self, token: str) -> SessionData | None:
        codec = self._current()
        return None if codec is None else codec.open(token)
