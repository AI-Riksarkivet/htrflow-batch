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


@pytest.mark.parametrize(
    "width,expected",
    [
        ("3000", "2500,"),  # numeric string, wide -> capped
        ("1200", "max"),  # numeric string, narrow -> max
        ("abc", "2500,"),  # junk -> cap (a 400 falls back to max in fetch)
        (None, "2500,"),
        ([3000], "2500,"),
    ],
)
def test_non_int_canvas_width_does_not_crash(width, expected):
    """W11: a non-int width raised TypeError and was retried to the cap."""
    m = {"items": [_canvas_with_service(width, 4000)]}
    pages = pages_from_manifest(m, width=2500)
    assert (
        pages[0].image_url == f"https://img/iiif/page-1/full/{expected}/0/default.jpg"
    )


@pytest.mark.parametrize(
    "canvas",
    [
        {"items": "x"},  # not a list of annotation pages
        {"items": [{"items": [{"body": "https://img/1.jpg"}]}]},  # body is a string
        {"items": [{"items": "x"}]},
        {"images": [{"resource": "https://img/1.jpg"}]},  # P2, same shape error
    ],
)
def test_malformed_canvas_is_permanent(canvas):
    """A junk canvas shape used to raise AttributeError/TypeError -> exit 1
    and three retries of a condition that cannot change (W11 fixed the same
    class of bug for widths)."""
    with pytest.raises(ManifestError, match="canvas 1"):
        pages_from_manifest({"items": [canvas]}, width=2500)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://h/1.jpg", "/rel.jpg"])
def test_non_http_canvas_image_url_is_permanent(url):
    """check_http_url ran for IMAGES only; a file:/ftp: body id failed per
    page inside httpx (UnsupportedProtocol) and was retried as transient."""
    canvas = {"items": [{"items": [{"body": {"id": url}}]}]}
    with pytest.raises(ManifestError, match="http"):
        pages_from_manifest({"items": [canvas]}, width=2500)


def test_manifest_items_that_are_not_a_list_are_permanent():
    with pytest.raises(ManifestError, match="not a list of canvases"):
        pages_from_manifest({"items": "x"}, width=2500)
    with pytest.raises(ManifestError):
        pages_from_manifest({"sequences": {"canvases": []}}, width=2500)


@pytest.mark.parametrize(
    "manifest", [{}, {"items": []}, {"sequences": [{"canvases": []}]}]
)
def test_an_empty_manifest_says_it_has_no_canvases(manifest):
    """An empty manifest is empty, not malformed: "items are not a list of
    canvases" sent the operator looking for a shape bug that is not there."""
    with pytest.raises(ManifestError, match="manifest has no canvases"):
        pages_from_manifest(manifest, width=2500)


def test_redact_url():
    from htrflow_batch.iiif import redact_url, redact_urls

    assert (
        redact_url("https://u:p@h:8443/a/b.json?token=S#f") == "https://h:8443/a/b.json"
    )
    assert redact_url("http://h/x") == "http://h/x"
    assert redact_url("not a url") == "not a url"
    assert (
        redact_urls("bad https://u:p@h/a?x=1 and http://h2/b?y=2 end")
        == "bad https://h/a and http://h2/b end"
    )


@pytest.mark.parametrize(
    "text, shown",
    [
        (
            "GET https://h/img(1).jpg?token=SECRET failed",
            "GET https://h/img(1).jpg failed",
        ),
        ("see [https://h/a]?token=SECRET]", "see [https://h/a]"),
        ("url='https://h/it's.jpg?token=SECRET'", "url='https://h/it's.jpg'"),
        ("(https://h/a?token=SECRET).", "(https://h/a)."),
        ("https://h/a?token=SECRET, then", "https://h/a, then"),
        ("<https://h/a?token=SECRET>", "<https://h/a>"),
    ],
)
def test_redact_urls_takes_the_query_off_a_url_with_punctuation_in_it(text, shown):
    """Audit 0923 W-3: the URL pattern stopped at `)`, `]` and `'`, so the
    query after `img(1).jpg` was never reached and its token went into the
    world-readable run log, the termination message and manifest.json."""
    from htrflow_batch.iiif import redact_urls

    assert "SECRET" not in redact_urls(text)
    assert redact_urls(text).startswith(shown)


@pytest.mark.parametrize(
    "text, shown",
    [
        (
            "['https://a/x?t=1','https://b/y?token=2']",
            "['https://a/x','https://b/y']",
        ),
        ("('https://a/x?t=1', 'https://b/y?t=2')", "('https://a/x', 'https://b/y')"),
        ("url=https://a/x?t=1&a=b|other", "url=https://a/x|other"),
        ("https://a/x?t=1,https://b/y?t=2", "https://a/x,https://b/y"),
        (
            "GET https://h/full/2500,/0/default.jpg?t=1",
            "GET https://h/full/2500,/0/default.jpg",
        ),
        ("{https://a/x?t=1}", "{https://a/x}"),
    ],
)
def test_redact_urls_keeps_the_text_around_each_url(text, shown):
    """Review M-3: a URL that ran to the next whitespace swallowed whatever
    followed it -- the second URL of a list, `|other` after a query. It ends
    at a character no URL holds unencoded, or at a quote, bracket or comma
    that closes it; the IIIF size comma inside one stays."""
    from htrflow_batch.iiif import redact_urls

    assert redact_urls(text) == shown


def test_source_digest_keeps_the_identifying_query():
    """W5: redact_url drops the whole query, so two pages a host selects with
    ``?id=`` were indistinguishable and an edited manifest never triggered a
    reprocess. The digest keeps the query without publishing it."""
    from htrflow_batch.iiif import source_digest

    one = source_digest("https://img.example/iiif?id=1")
    assert one != source_digest("https://img.example/iiif?id=2")
    assert one == source_digest("https://img.example/iiif?id=1")


@pytest.mark.parametrize(
    "credential",
    [
        "token=SECRET",
        "sig=SECRET",
        "signature=SECRET",
        "key=SECRET",
        "X-Amz-Signature=SECRET",
    ],
)
def test_source_digest_ignores_rotating_credentials(credential):
    """A tokenised URL differs from its stored form on every retry; only the
    part that names the image may reach the digest."""
    from htrflow_batch.iiif import source_digest

    bare = source_digest("https://img.example/iiif?id=1")
    assert source_digest(f"https://img.example/iiif?id=1&{credential}") == bare
    assert source_digest("https://u:pw@img.example/iiif?id=1") == bare


#: One image, signed twice: every signing scheme puts a new expiry and a new
#: signature on the URL at each manifest fetch (audit 0923 W-2).
SIGNED_TWICE = {
    "azure-sas": (
        "https://acct.blob.core.windows.net/c/0001.jpg?sv=2022-11-02&ss=b&srt=o"
        "&sp=r&se=2026-09-23T10:00:00Z&st=2026-09-23T09:00:00Z&spr=https&sig=AAA",
        "https://acct.blob.core.windows.net/c/0001.jpg?sv=2022-11-02&ss=b&srt=o"
        "&sp=r&se=2026-09-24T10:00:00Z&st=2026-09-24T09:00:00Z&spr=https&sig=BBB",
    ),
    "azure-service-sas": (
        "https://acct.blob.core.windows.net/c/0001.jpg?sp=r&st=1&se=2&sr=b&sig=AAA",
        "https://acct.blob.core.windows.net/c/0001.jpg?sp=r&st=3&se=4&sr=b&sig=BBB",
    ),
    "gcs-v4": (
        "https://storage.googleapis.com/b/0001.jpg?X-Goog-Algorithm=GOOG4-RSA-SHA256"
        "&X-Goog-Credential=sa%2F20260923&X-Goog-Date=20260923T090000Z"
        "&X-Goog-Expires=900&X-Goog-SignedHeaders=host&X-Goog-Signature=aaa",
        "https://storage.googleapis.com/b/0001.jpg?X-Goog-Algorithm=GOOG4-RSA-SHA256"
        "&X-Goog-Credential=sa%2F20260924&X-Goog-Date=20260924T090000Z"
        "&X-Goog-Expires=900&X-Goog-SignedHeaders=host&X-Goog-Signature=bbb",
    ),
    "gcs-v2": (
        "https://storage.googleapis.com/b/0001.jpg?GoogleAccessId=sa&Expires=1&Signature=a",
        "https://storage.googleapis.com/b/0001.jpg?GoogleAccessId=sa&Expires=2&Signature=b",
    ),
    "cloudfront-canned": (
        "https://d1.cloudfront.net/0001.jpg?Expires=1&Signature=a&Key-Pair-Id=K1",
        "https://d1.cloudfront.net/0001.jpg?Expires=2&Signature=b&Key-Pair-Id=K2",
    ),
    "cloudfront-custom": (
        "https://d1.cloudfront.net/0001.jpg?Policy=p1&Signature=a&Key-Pair-Id=K1",
        "https://d1.cloudfront.net/0001.jpg?Policy=p2&Signature=b&Key-Pair-Id=K1",
    ),
    "s3-v2": (
        "https://b.s3.amazonaws.com/0001.jpg?AWSAccessKeyId=A&Expires=1&Signature=a",
        "https://b.s3.amazonaws.com/0001.jpg?AWSAccessKeyId=A&Expires=2&Signature=b",
    ),
    "s3-v4": (
        "https://b.s3.amazonaws.com/0001.jpg?X-Amz-Date=1&X-Amz-Expires=9"
        "&X-Amz-Security-Token=t1&X-Amz-Signature=a",
        "https://b.s3.amazonaws.com/0001.jpg?X-Amz-Date=2&X-Amz-Expires=9"
        "&X-Amz-Security-Token=t2&X-Amz-Signature=b",
    ),
    "akamai": (
        "https://cdn.example/0001.jpg?hdnts=exp=1~hmac=a",
        "https://cdn.example/0001.jpg?hdnts=exp=2~hmac=b",
    ),
}


@pytest.mark.parametrize("scheme", sorted(SIGNED_TWICE))
def test_source_digest_is_the_same_for_a_re_signed_url(scheme):
    """Audit 0923 W-2: only token/sig/signature/key and X-Amz-* came out, so
    an Azure SAS, GCS or CloudFront URL digested differently on every
    manifest fetch, resume deleted every done page as changed, and a volume
    needing more than one attempt never completed."""
    from htrflow_batch.iiif import source_digest

    first, second = SIGNED_TWICE[scheme]
    assert source_digest(first) == source_digest(second)


@pytest.mark.parametrize("scheme", sorted(SIGNED_TWICE))
def test_source_digest_still_sees_another_image_behind_a_signature(scheme):
    """What names the image is still a real change, signed or not."""
    from htrflow_batch.iiif import source_digest

    first, _ = SIGNED_TWICE[scheme]
    assert source_digest(first) != source_digest(first.replace("0001", "0002"))


def test_source_digest_keeps_ordinary_names_outside_their_signing_scheme():
    """`st`, `se`, `sp`, `Expires` and `Policy` are only a signing scheme's
    when its signature is there too; without it they may name the image."""
    from htrflow_batch.iiif import source_digest

    for query in ("st=1", "se=1", "sp=1", "sr=1", "Expires=1", "Policy=1"):
        base = "https://img.example/iiif?id=1&"
        assert source_digest(base + query) != source_digest(
            base + query.replace("1", "2")
        )


def test_source_digest_publishes_nothing_of_the_url():
    """It goes into the world-readable manifest.json (S6), so it must be a
    digest and nothing else."""
    from htrflow_batch.iiif import source_digest

    digest = source_digest("https://img.example/iiif?id=1")
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_painting_body_drops_a_body_with_a_javascript_id():
    """W6: the body is copied verbatim out of a third-party manifest into an
    iiif.json we publish under our own domain. Only the fetch URL was ever
    scheme-checked, and that is the service's -- so a canvas whose service is
    a normal image service and whose body id is `javascript:` published the
    `javascript:` id to every viewer that opened the volume."""
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"]["id"] = "javascript:alert(1)"
    assert painting_body(canvas) == {}


def test_painting_body_drops_a_body_with_a_javascript_service():
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"]["service"] = [{"id": "javascript:alert(1)"}]
    assert painting_body(canvas) == {}


def test_painting_body_drops_a_p2_resource_with_a_javascript_id(p2_manifest):
    canvas = p2_manifest["sequences"][0]["canvases"][0]
    canvas["images"][0]["resource"]["@id"] = "javascript:alert(1)"
    from htrflow_batch.iiif import painting_body

    assert painting_body(canvas) == {}


def test_painting_body_drops_a_body_that_is_not_an_object():
    """A bare-URL body (manifests in the wild carry them) must not be copied
    into the manifest as if it were one."""
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"] = "https://img/full/max/0/default.jpg"
    assert painting_body(canvas) == {}


def test_painting_body_takes_the_first_usable_item_of_a_choice():
    """W6 review: P3 lets a painting annotation offer a Choice -- several
    representations of the same page, the client picking one. Requiring a
    single body object dropped the image for every canvas shaped that way, so
    the first item we would publish is taken instead."""
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    anno = canvas["items"][0]["items"][0]
    anno["body"] = {
        "type": "Choice",
        "items": [
            {"id": "javascript:alert(1)", "type": "Image"},
            {"id": "https://img/colour.jpg", "type": "Image"},
        ],
    }
    assert painting_body(canvas)["id"] == "https://img/colour.jpg"


def test_painting_body_takes_the_first_usable_item_of_a_bare_list():
    """Manifests in the wild also put a plain list of bodies there."""
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"] = [
        {"id": "ftp://img/scan.jpg", "type": "Image"},
        {"id": "https://img/scan.jpg", "type": "Image"},
    ]
    assert painting_body(canvas)["id"] == "https://img/scan.jpg"


def test_painting_body_drops_a_choice_with_nothing_publishable():
    from htrflow_batch.iiif import painting_body

    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"] = {
        "type": "Choice",
        "items": [{"id": "javascript:alert(1)", "type": "Image"}],
    }
    assert painting_body(canvas) == {}


def _fetched_and_published(canvas):
    """What the wrapper downloads for a canvas and what it publishes for it."""
    from htrflow_batch.iiif import painting_body

    page = pages_from_manifest({"items": [canvas]}, width=2500)[0]
    return page.image_url, painting_body(page.canvas)


def test_a_choice_body_is_fetched_as_the_image_it_publishes():
    """3097: the fetch URL was chosen by its own rule, which did not know a
    Choice -- so a canvas the viewer manifest publishes fine failed the whole
    volume with 'no image'."""
    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"] = {
        "type": "Choice",
        "items": [
            {"id": "javascript:alert(1)", "type": "Image"},
            {
                "id": "https://img/colour.jpg",
                "type": "Image",
                "service": [{"id": "https://img/iiif/colour"}],
            },
        ],
    }
    url, body = _fetched_and_published(canvas)
    assert url == "https://img/iiif/colour/full/2500,/0/default.jpg"
    assert body["id"] == "https://img/colour.jpg"


def test_a_list_body_is_fetched_as_the_image_it_publishes():
    """3097: a bare list of bodies raised AttributeError -- exit 13."""
    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"] = [
        {"id": "ftp://img/scan.jpg", "type": "Image"},
        {"id": "https://img/scan.jpg", "type": "Image"},
    ]
    assert _fetched_and_published(canvas) == (
        "https://img/scan.jpg",
        {"id": "https://img/scan.jpg", "type": "Image"},
    )


def test_an_unpublishable_first_body_does_not_pick_the_fetched_image():
    """3097: with an http service on an unpublishable first body, image A was
    fetched and transcribed while image B was published -- the ALTO drawn
    over the wrong picture."""
    canvas = _canvas_with_service(3000, 4000)
    first = canvas["items"][0]["items"][0]
    first["body"]["id"] = "javascript:alert(1)"  # service stays http
    second = {
        "body": {
            "id": "https://img/b.jpg",
            "service": [{"id": "https://img/iiif/b"}],
        }
    }
    canvas["items"][0]["items"].append(second)
    url, body = _fetched_and_published(canvas)
    assert url == "https://img/iiif/b/full/2500,/0/default.jpg"
    assert body["id"] == "https://img/b.jpg"


def test_a_canvas_with_nothing_publishable_is_fetched_but_not_published():
    """W6 still holds: a body we will not publish costs the canvas its image
    in the viewer and nothing else -- the page is fetched and transcribed."""
    canvas = _canvas_with_service(3000, 4000)
    canvas["items"][0]["items"][0]["body"]["id"] = "javascript:alert(1)"
    url, body = _fetched_and_published(canvas)
    assert url == "https://img/iiif/page-1/full/2500,/0/default.jpg"
    assert body == {}


def test_p2_takes_the_first_publishable_image_for_both(p2_manifest):
    canvas = p2_manifest["sequences"][0]["canvases"][0]
    good = {"resource": {"@id": "http://ex/b.jpg", "service": {"@id": "http://ex/b"}}}
    canvas["images"][0]["resource"]["@id"] = "javascript:alert(1)"
    canvas["images"].append(good)
    url, body = _fetched_and_published(canvas)
    assert url.startswith("http://ex/b/full/")
    assert body["id"] == "http://ex/b.jpg"


def test_fetch_manifest_gzip_bomb_is_capped_in_bounded_memory(gzip_bomb, peak_mib):
    """3062: the manifest cap is on the decoded JSON, counted while inflating."""
    bomb = gzip_bomb(256, head=b'{"items": "')
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(bomb)
            )
        )
    )

    def fetch():
        with pytest.raises(ManifestError, match="too large"):
            fetch_manifest("https://x/manifest", client, max_bytes=1 << 20)

    assert peak_mib(fetch) < 16


def test_fetch_manifest_gzip_under_cap_ok(sample_manifest):
    import gzip
    import json

    body = gzip.compress(json.dumps(sample_manifest).encode())
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(body)
            )
        )
    )
    assert fetch_manifest("https://x/manifest", client)["type"] == "Manifest"


def test_fetch_manifest_refuses_an_encoding_it_did_not_ask_for():
    seen = []

    def handler(req):
        seen.append(req.headers.get("Accept-Encoding"))
        return httpx.Response(
            200, headers={"Content-Encoding": "br"}, stream=httpx.ByteStream(b"x")
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ManifestError, match="Content-Encoding"):
        fetch_manifest("https://x/manifest", client)
    assert seen == ["gzip"]


def test_fetch_manifest_slow_drip_is_cut_off_and_transient(drip_server):
    """3063: the manifest fetch has the same wall-clock deadline; a host
    that is merely slow today may not be tomorrow, so it is retried."""
    import time

    from htrflow_batch.bounded import http_client

    t0 = time.monotonic()
    with (
        http_client() as client,
        pytest.raises(TransientManifestError, match="deadline"),
    ):
        fetch_manifest(f"{drip_server('head')}/manifest", client, deadline=0.5)
    assert time.monotonic() - t0 < 5
