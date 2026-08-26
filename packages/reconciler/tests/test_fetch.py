"""The manifest fetch is driven by campaign data (S5): http(s) only, byte
cap, redirect limit, short timeout — every failure is ``None`` (unreachable),
never an exception and never a multi-GB body in a 512 Mi pod."""

import json

import httpx
import pytest

from htrflow_reconciler.__main__ import fetch_json
from htrflow_reconciler.main import SourceRejected


def _client(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=3
    )


def test_fetch_json_returns_the_document():
    def handler(request):
        return httpx.Response(200, json={"items": [1]})

    assert fetch_json(
        "https://x/manifest", max_bytes=1 << 20, client=_client(handler)
    ) == {"items": [1]}


def test_fetch_json_rejects_non_http_schemes():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={})

    for url in ("file:///etc/passwd", "ftp://x/m", "javascript:alert(1)"):
        with pytest.raises(SourceRejected) as e:
            fetch_json(url, max_bytes=1 << 20, client=_client(handler))
        assert e.value.format == "unsupported"
    assert calls == []


def test_fetch_json_caps_the_body_by_header_and_by_stream():
    big = json.dumps({"pad": "x" * 5000}).encode()

    def declared(request):
        return httpx.Response(
            200, content=big, headers={"content-length": str(len(big))}
        )

    def undeclared(request):
        return httpx.Response(
            200, content=big, headers={"transfer-encoding": "chunked"}
        )

    with pytest.raises(SourceRejected):
        fetch_json("https://x/m", max_bytes=1000, client=_client(declared))
    with pytest.raises(SourceRejected):
        fetch_json("https://x/m", max_bytes=1000, client=_client(undeclared))
    assert (
        fetch_json("https://x/m", max_bytes=10000, client=_client(undeclared))
        is not None
    )


def test_fetch_json_limits_redirects_and_tolerates_junk():
    def loop(request):
        return httpx.Response(302, headers={"location": "https://x/again"})

    def junk(request):
        return httpx.Response(200, content=b"<html>not json</html>")

    def not_found(request):
        return httpx.Response(404)

    assert fetch_json("https://x/m", max_bytes=1 << 20, client=_client(loop)) is None
    with pytest.raises(SourceRejected) as e:
        fetch_json("https://x/m", max_bytes=1 << 20, client=_client(junk))
    assert e.value.format == "unsupported"
    with pytest.raises(SourceRejected) as e:
        fetch_json("https://x/m", max_bytes=1 << 20, client=_client(not_found))
    assert e.value.format == "unreachable"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_client_errors_are_permanent(status):
    """Mirrors the wrapper's permanent set exactly (A2 contract)."""
    with pytest.raises(SourceRejected) as e:
        fetch_json(
            "https://x/m",
            max_bytes=1 << 20,
            client=_client(lambda r: httpx.Response(status)),
        )
    assert e.value.format == "unreachable"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_server_errors_and_throttling_are_transient(status):
    assert (
        fetch_json(
            "https://x/m",
            max_bytes=1 << 20,
            client=_client(lambda r: httpx.Response(status)),
        )
        is None
    )


def test_non_object_json_is_permanent():
    with pytest.raises(SourceRejected) as e:
        fetch_json(
            "https://x/m",
            max_bytes=1 << 20,
            client=_client(lambda r: httpx.Response(200, json=[1, 2])),
        )
    assert e.value.format == "unsupported"


def test_network_error_is_transient():
    def boom(request):
        raise httpx.ConnectError("dns")

    assert fetch_json("https://x/m", max_bytes=1 << 20, client=_client(boom)) is None
