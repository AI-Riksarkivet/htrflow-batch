"""What the results proxy may serve, and how (docs: security, "Results")."""

from __future__ import annotations

import re
from urllib.parse import unquote

FILE_HEADERS = {
    "Cache-Control": "private, no-cache",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

_PASSED = ("application/json", "application/xml", "text/xml", "text/plain")
_ENCODED_SEPARATOR = re.compile(r"%(2f|5c)", re.IGNORECASE)


def allowed_key(raw_path: str, namespace: str) -> str | None:
    """The object key for a request path, or None when it is not a result
    key. Decoded exactly once; an encoded separator is refused outright, so
    a decoded key can never gain a segment the raw path did not show."""
    if _ENCODED_SEPARATOR.search(raw_path):
        return None
    key = unquote(raw_path)
    if "\\" in key:
        return None
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if len(parts) >= 2 and parts[0] == namespace:
        return key
    if len(parts) >= 3 and parts[:2] == ["status", "logs"]:
        return key
    return None


def served_type(stored: str | None) -> tuple[str, bool]:
    base = (stored or "").split(";", 1)[0].strip().lower()
    if base in _PASSED:
        assert stored is not None  # Guaranteed: if stored is None, base is ""
        return stored, False
    return "application/octet-stream", True
