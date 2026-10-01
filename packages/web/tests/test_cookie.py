import re
from pathlib import Path

import pytest

from htrflow_web.cookie import browser_cookie, forwarded

SRC = Path(__file__).parent.parent / "src" / "htrflow_web"


def test_the_cookie_name_is_spelled_once():
    """Every module that reads, forwards or sets the session cookie imports
    its name: a copy left behind by a rename fails open to "not logged in"
    only by luck. (The AEAD's associated data is bytes, and a separate
    constant on purpose: it must not change when the name does.)"""
    spelled = [
        path.name
        for path in SRC.rglob("*.py")
        for line in path.read_text().splitlines()
        if re.search(r'(?<![b\w])"htr_session', line)
    ]
    assert spelled == ["cookie.py"]


@pytest.mark.parametrize(
    "headers,scheme,want",
    [
        ({"host": "pod:8082"}, "http", ("http", "pod:8082")),
        (
            {"host": "pod:8082", "x-forwarded-proto": "https", "x-forwarded-host": "h"},
            "http",
            ("https", "h"),
        ),
        # Each falls back on its own: an edge that forwards only the scheme
        # still leaves the Host it was asked for in place.
        ({"host": "h", "x-forwarded-proto": "https"}, "http", ("https", "h")),
        ({"host": "pod", "x-forwarded-host": "h"}, "https", ("https", "h")),
    ],
)
def test_forwarded_is_the_browsers_scheme_and_host(headers, scheme, want):
    assert forwarded(headers, scheme) == want


@pytest.mark.parametrize(
    "headers,scheme,want",
    [
        ({"host": "h"}, "https", "__Host-htr_session"),
        ({"host": "pod", "x-forwarded-proto": "https"}, "http", "__Host-htr_session"),
        ({"host": "h"}, "http", "htr_session"),
        ({"host": "h", "x-forwarded-proto": "http"}, "https", "htr_session"),
    ],
)
def test_over_https_the_cookie_is_host_prefixed(headers, scheme, want):
    """`__Host-`: the browser takes it only from a secure answer for this
    exact host, so a sibling subdomain or a plain-HTTP answer cannot plant a
    session. Plain HTTP (dev) cannot carry the prefix."""
    assert browser_cookie(headers, scheme) == want
