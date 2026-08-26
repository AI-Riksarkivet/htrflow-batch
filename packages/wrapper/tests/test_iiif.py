import httpx
import pytest

from htrflow_batch.iiif import (
    ManifestError,
    TransientManifestError,
    fetch_manifest,
    pages_from_manifest,
)


def test_pages_from_manifest(sample_manifest):
    pages = pages_from_manifest(sample_manifest, width=2500)
    assert [p.name for p in pages] == ["0001", "0002", "0003"]
    assert pages[0].index == 1
    assert pages[0].image_url == (
        "https://iiif.example/mock-vol/page-00001/full/2500,/0/default.jpg"
    )
    assert pages[0].canvas["type"] == "Canvas"


def test_pages_without_service_falls_back_to_body_id(sample_manifest):
    del sample_manifest["items"][0]["items"][0]["items"][0]["body"]["service"]
    pages = pages_from_manifest(sample_manifest, width=2500)
    # no service -> use the painting body URL as-is (no width control)
    assert pages[0].image_url.endswith("/full/max/0/default.jpg")


def test_empty_manifest_raises():
    with pytest.raises(ManifestError):
        pages_from_manifest({"items": []}, width=2500)


def test_fetch_manifest_ok(sample_manifest):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=sample_manifest)
    )
    client = httpx.Client(transport=transport)
    m = fetch_manifest("https://x/manifest", client)
    assert m["type"] == "Manifest"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_fetch_manifest_4xx_is_permanent(status):
    transport = httpx.MockTransport(lambda req: httpx.Response(status))
    client = httpx.Client(transport=transport)
    with pytest.raises(ManifestError) as ei:
        fetch_manifest("https://x/manifest", client)
    assert not isinstance(ei.value, TransientManifestError)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_fetch_manifest_5xx_and_429_are_transient(status):
    """W1: a gateway blip must not park the volume in needs-attention."""
    transport = httpx.MockTransport(lambda req: httpx.Response(status))
    client = httpx.Client(transport=transport)
    with pytest.raises(TransientManifestError):
        fetch_manifest("https://x/manifest", client)


def test_transient_manifest_error_is_not_permanent():
    # main.py classifies ManifestError as exit 13; the transient one must
    # fall through to the generic (retryable) branch.
    assert not issubclass(TransientManifestError, ManifestError)


@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ReadTimeout])
def test_fetch_manifest_network_error_is_transient(exc):
    def handler(req):
        raise exc("boom", request=req)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(TransientManifestError):
        fetch_manifest("https://x/manifest", client)


def test_fetch_manifest_non_json_is_permanent():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<html>login</html>")
    )
    client = httpx.Client(transport=transport)
    with pytest.raises(ManifestError, match="not JSON"):
        fetch_manifest("https://x/manifest", client)


def test_fetch_manifest_non_object_json_is_permanent():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[1, 2]))
    client = httpx.Client(transport=transport)
    with pytest.raises(ManifestError, match="not a JSON object"):
        fetch_manifest("https://x/manifest", client)


def test_fetch_manifest_rejects_non_http_scheme():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ManifestError, match="http"):
        fetch_manifest("ftp://x/manifest", client)
    with pytest.raises(ManifestError, match="http"):
        fetch_manifest("file:///etc/passwd", client)
    assert calls == []


def test_fetch_manifest_content_length_over_cap_is_permanent():
    def handler(req):
        return httpx.Response(200, headers={"Content-Length": "999"}, content=b"")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ManifestError, match="too large"):
        fetch_manifest("https://x/manifest", client, max_bytes=100)


def test_fetch_manifest_streamed_body_over_cap_is_permanent():
    """No Content-Length (chunked): the cap must apply to the bytes read."""

    def handler(req):
        return httpx.Response(200, stream=httpx.ByteStream(b"[" + b"1," * 200 + b"1]"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ManifestError, match="too large"):
        fetch_manifest("https://x/manifest", client, max_bytes=100)


def test_fetch_manifest_under_cap_ok(sample_manifest):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=sample_manifest)
    )
    client = httpx.Client(transport=transport)
    m = fetch_manifest("https://x/manifest", client, max_bytes=1 << 20)
    assert m["type"] == "Manifest"


def _canvas_with_service(width, height):
    return {
        "width": width,
        "height": height,
        "items": [
            {
                "items": [
                    {
                        "body": {
                            "id": "https://img/full/max/0/default.jpg",
                            "service": [{"id": "https://img/iiif/page-1"}],
                        }
                    }
                ]
            }
        ],
    }


def test_narrow_canvas_requests_max_not_upscale():
    """A canvas narrower than the width cap must request full/max — level1
    IIIF servers (lbiiif) reject upscaling with 400."""
    m = {"items": [_canvas_with_service(1281, 3743)]}
    pages = pages_from_manifest(m, width=2500)
    assert pages[0].image_url == "https://img/iiif/page-1/full/max/0/default.jpg"


def test_wide_canvas_still_width_capped():
    m = {"items": [_canvas_with_service(3494, 2472)]}
    pages = pages_from_manifest(m, width=2500)
    assert pages[0].image_url == "https://img/iiif/page-1/full/2500,/0/default.jpg"


def test_p2_manifest_yields_pages(p2_manifest):
    pages = pages_from_manifest(p2_manifest, width=2500)
    assert len(pages) == 1
    assert pages[0].name == "0001"
    assert pages[0].image_url == "http://ex/img/full/2500,/0/default.jpg"


def test_p2_narrow_canvas_requests_max(p2_manifest):
    p2_manifest["sequences"][0]["canvases"][0]["width"] = 1200
    pages = pages_from_manifest(p2_manifest, width=2500)
    assert pages[0].image_url == "http://ex/img/full/max/0/default.jpg"


def test_p2_resource_without_service_uses_direct_url(p2_manifest):
    del p2_manifest["sequences"][0]["canvases"][0]["images"][0]["resource"]["service"]
    pages = pages_from_manifest(p2_manifest, width=2500)
    assert pages[0].image_url == "http://ex/img/full/full/0/default.jpg"


def test_painting_body_p2_emits_v2_style_service(p2_manifest):
    from htrflow_batch.iiif import painting_body

    body = painting_body(p2_manifest["sequences"][0]["canvases"][0])
    assert body["id"] == "http://ex/img/full/full/0/default.jpg"
    assert body["type"] == "Image"
    svc = body["service"][0]
    assert svc["@id"] == "http://ex/img"
    assert svc["@type"] == "ImageService2"
    assert "profile" in svc


def test_painting_body_p3_passthrough():
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    body = painting_body(canvas)
    assert body["service"][0]["id"] == "https://img/iiif/page-1"
