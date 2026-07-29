import httpx
import pytest

from htrflow_batch.iiif import (
    ManifestError,
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


def test_fetch_manifest_404_raises():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    client = httpx.Client(transport=transport)
    with pytest.raises(ManifestError):
        fetch_manifest("https://x/manifest", client)


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
