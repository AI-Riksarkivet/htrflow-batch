"""The manifest fetch is driven by campaign data (S5): http(s) only, byte
cap, redirect limit, short timeout — every failure is ``None`` (unreachable),
never an exception and never a multi-GB body in a 512 Mi pod."""

import json

import httpx

from htrflow_reconciler.__main__ import fetch_json


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
        assert fetch_json(url, max_bytes=1 << 20, client=_client(handler)) is None
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

    assert fetch_json("https://x/m", max_bytes=1000, client=_client(declared)) is None
    assert fetch_json("https://x/m", max_bytes=1000, client=_client(undeclared)) is None
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
    assert fetch_json("https://x/m", max_bytes=1 << 20, client=_client(junk)) is None
    assert (
        fetch_json("https://x/m", max_bytes=1 << 20, client=_client(not_found)) is None
    )
