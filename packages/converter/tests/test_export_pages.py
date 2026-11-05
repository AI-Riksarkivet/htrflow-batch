"""Page rules for `htrflow-campaigns export`: sets, file numbers, quality mean."""

import pytest

from htrflow_converter.export_pages import (
    PageRuleError,
    info_url,
    manifest_sizes,
    page_number,
    parse_page_set,
    quality_mean,
)

RA = "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/full/2500,/0/default.jpg"


def test_page_sets():
    assert parse_page_set("1") == {1}
    assert parse_page_set("1,3-4") == {1, 3, 4}
    assert parse_page_set("none") == frozenset()
    for bad in ("0", "a", "4-2", "", "1,,2"):
        with pytest.raises(PageRuleError):
            parse_page_set(bad)


def test_number_comes_from_the_source_image_id():
    assert page_number(RA, "R0001203") == 1
    assert page_number(RA.replace("_00001", "_00417"), "R0001203") == 417


def test_a_source_that_is_not_this_volume_is_refused():
    for url in (
        RA.replace("R0001203_", "R0009999_"),  # another volume
        RA.replace("_00001", "_0001"),  # four digits
        "https://example.org/scan.jpg",  # no image id
    ):
        with pytest.raises(PageRuleError):
            page_number(url, "R0001203")


def test_info_url_is_the_image_service_base():
    assert (
        info_url(RA)
        == "https://lbiiif.riksarkivet.se/v3/arkis!R0001203_00001/info.json"
    )
    with pytest.raises(PageRuleError):
        info_url("https://example.org/scan.jpg")


def test_quality_mean_leaves_out_zero_unscored_and_emptied_pages():
    results = {
        "0001": {"status": "ok", "quality": 0.2},  # emptied below
        "0002": {"status": "ok", "quality": 0.9},
        "0003": {"status": "ok", "quality": 0.0},  # QP 0
        "0004": {"status": "failed"},  # unscored
        "0005": {"status": "skipped", "quality": 0.7},
    }
    assert quality_mean(results, {"0001"}) == (pytest.approx(0.8), 2)
    assert quality_mean({"0001": {"status": "failed"}}, set()) == (None, 0)


# --- sizes from a downloaded IIIF manifest (--manifests) --------------------

MAN = "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest"


def ra_source(n):
    return f"https://lbiiif.riksarkivet.se/v3/arkis!R0001203_{n:05d}/full/2500,/0/default.jpg"


def p3_canvas(n, w=3574, h=2543, body_size=True):
    ident = f"arkis!R0001203_{n:05d}"
    body = {
        "id": f"https://lbiiif.riksarkivet.se/{ident}/full/max/0/default.jpg",
        "type": "Image",
        "service": [
            {"id": f"https://lbiiif.riksarkivet.se/v3/{ident}", "width": 0},
            {"@id": f"https://lbiiif.riksarkivet.se/v2/{ident}", "width": 0},
        ],
    }
    if body_size:
        body |= {"width": w, "height": h}
    return {
        "id": f"https://lbiiif.riksarkivet.se/{ident}/canvas",
        "type": "Canvas",
        "width": w,
        "height": h,
        "items": [{"items": [{"body": body}]}],
    }


def p3(*canvases, ident=MAN):
    return {"id": ident, "type": "Manifest", "items": list(canvases)}


SOURCES = {"0001": ra_source(1), "0002": ra_source(2)}


def test_sizes_come_from_each_page_canvas():
    doc = p3(p3_canvas(1), p3_canvas(2, 1281, 3743, body_size=False))
    assert manifest_sizes(doc, MAN, SOURCES) == {
        "0001": (3574, 2543),
        "0002": (1281, 3743),
    }


def test_a_presentation_2_manifest_is_read_too():
    def canvas(n, w, h):
        sid = f"https://lbiiif.riksarkivet.se/v3/arkis!R0001203_{n:05d}"
        res = {"@id": "x", "width": w, "height": h, "service": {"@id": sid}}
        return {"@id": f"c{n}", "width": w, "height": h, "images": [{"resource": res}]}

    doc = {
        "@id": MAN,
        "sequences": [{"canvases": [canvas(1, 10, 20), canvas(2, 30, 40)]}],
    }
    assert manifest_sizes(doc, MAN, SOURCES) == {"0001": (10, 20), "0002": (30, 40)}


def test_the_manifest_of_another_volume_is_refused():
    other = "https://lbiiif.riksarkivet.se/arkis!R0009999/manifest"
    with pytest.raises(PageRuleError, match="R0009999.*not the volume's"):
        manifest_sizes(p3(p3_canvas(1), p3_canvas(2), ident=other), MAN, SOURCES)


def test_a_page_whose_image_is_not_in_the_manifest_is_named():
    with pytest.raises(PageRuleError, match="page 0002: its image is not in"):
        manifest_sizes(p3(p3_canvas(1)), MAN, SOURCES)


def test_an_image_on_two_canvases_is_refused():
    with pytest.raises(PageRuleError, match="page 0001: .*two canvases"):
        manifest_sizes(p3(p3_canvas(1), p3_canvas(1), p3_canvas(2)), MAN, SOURCES)


@pytest.mark.parametrize("w,h", [(0, 2543), (None, 2543), (True, 1), (3574.0, 2543)])
def test_a_canvas_without_a_real_size_is_refused(w, h):
    doc = p3(p3_canvas(1, w, h, body_size=False), p3_canvas(2))
    with pytest.raises(PageRuleError, match="page 0001: canvas size"):
        manifest_sizes(doc, MAN, SOURCES)


def test_an_image_size_that_disagrees_with_its_canvas_is_refused():
    bad = p3_canvas(1)
    bad["items"][0]["items"][0]["body"]["width"] = 1787
    with pytest.raises(PageRuleError, match="page 0001: the image is 1787x2543"):
        manifest_sizes(p3(bad, p3_canvas(2)), MAN, SOURCES)


@pytest.mark.parametrize("doc", [[], {"id": MAN}, {"id": MAN, "items": "x"}])
def test_a_document_that_is_not_a_manifest_is_refused(doc):
    with pytest.raises(PageRuleError):
        manifest_sizes(doc, MAN, SOURCES)


def test_a_body_that_is_not_an_object_is_refused():
    doc = p3({"items": [{"items": [{"body": "u"}]}]}, p3_canvas(1), p3_canvas(2))
    with pytest.raises(PageRuleError, match="canvas 1"):
        manifest_sizes(doc, MAN, SOURCES)


@pytest.mark.parametrize(
    "canvas",
    [
        {"items": 5},
        {"items": [{"items": 5}]},
        {"images": 7},
        {"items": [5, {"items": [7, {"body": [1]}]}]},
    ],
)
def test_a_canvas_of_the_wrong_shape_is_a_refusal_not_a_crash(canvas):
    with pytest.raises(PageRuleError):
        manifest_sizes(p3(canvas, p3_canvas(1)), MAN, SOURCES)


def test_one_canvas_naming_its_image_twice_is_one_canvas():
    one = p3_canvas(1)
    body = one["items"][0]["items"][0]["body"]
    body["service"].append({"id": body["service"][0]["id"] + "/"})
    one["items"][0]["items"].append({"body": body})
    assert manifest_sizes(p3(one, p3_canvas(2)), MAN, SOURCES)["0001"] == (3574, 2543)


def test_an_escaped_service_id_still_matches():
    one = p3_canvas(1)
    sid = one["items"][0]["items"][0]["body"]["service"][0]
    sid["id"] = sid["id"].replace("!", "%21")
    assert "0001" in manifest_sizes(p3(one, p3_canvas(2)), MAN, SOURCES)


def test_a_stated_image_size_that_is_not_a_size_is_refused():
    bad = p3_canvas(1)
    bad["items"][0]["items"][0]["body"]["width"] = "3574"
    with pytest.raises(PageRuleError, match="page 0001: the image states"):
        manifest_sizes(p3(bad, p3_canvas(2)), MAN, SOURCES)
