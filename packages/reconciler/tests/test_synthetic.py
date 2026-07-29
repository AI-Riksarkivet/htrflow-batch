from htrflow_reconciler.synthetic import build_manifest, classify_manifest


def test_build_manifest_one_canvas_per_image():
    m = build_manifest(
        "loose", ["http://x/1.jpg", "http://x/2.jpg"], "http://s3/loose/manifest.json"
    )
    assert m["@context"] == "http://iiif.io/api/presentation/3/context.json"
    assert m["id"] == "http://s3/loose/manifest.json"
    assert len(m["items"]) == 2
    body = m["items"][0]["items"][0]["items"][0]["body"]
    assert body == {"id": "http://x/1.jpg", "type": "Image", "format": "image/jpeg"}
    anno = m["items"][0]["items"][0]["items"][0]
    assert anno["motivation"] == "painting"
    assert anno["target"] == m["items"][0]["id"]


def test_classify_manifest():
    assert classify_manifest({"items": [{}]}) == "p3"
    assert classify_manifest({"sequences": [{"canvases": [{}]}]}) == "p2"
    assert classify_manifest({"collections": []}) == "unsupported"
    assert classify_manifest({}) == "unsupported"
