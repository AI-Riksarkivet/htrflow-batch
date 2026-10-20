import json

import pytest

from htrflow_batch import safexml
from htrflow_batch.iiif import pages_from_manifest
from htrflow_batch.viewer import build_viewer_manifest, parse_alto_dims

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="2500" HEIGHT="3538" ID="p1"/></Layout>
</alto>"""


def test_parse_alto_dims(tmp_path):
    p = tmp_path / "0001.xml"
    p.write_text(ALTO)
    assert parse_alto_dims(safexml.fromstring(p.read_bytes())) == (2500, 3538)


def test_parse_alto_dims_missing(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<alto/>")
    with pytest.raises(ValueError):
        parse_alto_dims(safexml.fromstring(p.read_bytes()))


def test_parse_alto_dims_page_priority_over_nested_printspace(tmp_path):
    """Regression test: PrintSpace nested in Page should not mask Page dims."""
    alto_nested = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout>
    <Page WIDTH="2500" HEIGHT="3538" ID="p1">
      <PrintSpace WIDTH="2400" HEIGHT="3400"/>
    </Page>
  </Layout>
</alto>"""
    p = tmp_path / "nested.xml"
    p.write_text(alto_nested)
    assert parse_alto_dims(safexml.fromstring(p.read_bytes())) == (2500, 3538)


def test_build_viewer_manifest(sample_manifest, cfg):
    pages = pages_from_manifest(sample_manifest, width=2500)
    dims = {"0001": (2500, 3538), "0002": (2500, 3520)}  # page 3 unprocessed
    m = build_viewer_manifest(cfg, sample_manifest, pages, dims)
    assert m["type"] == "Manifest"
    assert m["id"] == "http://public/htr-results/demo-v1/SE-RA-1234/iiif.json"
    assert m["label"] == sample_manifest["label"]
    assert len(m["items"]) == 2  # only processed pages
    c1 = m["items"][0]
    assert (c1["width"], c1["height"]) == (2500, 3538)  # capped dims (D19)
    body = c1["items"][0]["items"][0]["body"]
    assert body["service"][0]["id"].endswith("page-00001")
    sa = c1["seeAlso"][0]
    assert sa["id"] == "http://public/htr-results/demo-v1/SE-RA-1234/alto/0001.xml"
    assert "alto" in sa["profile"]


def test_viewer_manifest_declares_search_service(sample_manifest, cfg):
    """UV4 (RA fork) gates the ALTO text panel on getSearchService() —
    without a search service entry the transcription panel never shows."""
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages, {"0001": (2500, 3538)})
    svc = m["service"][0]
    assert svc["profile"] == "http://iiif.io/api/search/1/search"
    assert svc["@id"] == "http://public/htr-results/demo-v1/SE-RA-1234/search"


def test_each_canvas_offers_its_xml_for_download(sample_manifest, cfg):
    """UV's download dialog lists a canvas's `rendering`, not its
    `seeAlso`: without these the dialog has no way to the page's ALTO."""
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages, {"0001": (2500, 3538)})
    vol = "http://public/htr-results/demo-v1/SE-RA-1234"
    assert m["items"][0]["rendering"] == [
        {
            "id": f"{vol}/alto/0001.xml",
            "type": "Text",
            "format": "application/xml",
            "label": {"none": ["ALTO"]},
        },
        {
            "id": f"{vol}/page/0001.xml",
            "type": "Text",
            "format": "application/xml",
            "label": {"none": ["PAGE"]},
        },
    ]


def _p2_viewer_manifest(cfg, p2_manifest) -> dict:
    pages = pages_from_manifest(p2_manifest, width=2500)
    return build_viewer_manifest(cfg, p2_manifest, pages, {"0001": (2500, 3333)})


def test_viewer_manifest_normalizes_p2_string_label(cfg, p2_manifest):
    """P2 canvas labels are plain strings; the published P3 manifest must
    carry dict labels or UV renders '[object Object]'-style breakage."""
    m = _p2_viewer_manifest(cfg, p2_manifest)
    c = m["items"][0]
    assert c["label"] == {"none": ["f. 1r"]}
    assert m["label"] == {"none": ["P2 vol"]}
    body = c["items"][0]["items"][0]["body"]
    assert body["service"][0]["@type"] == "ImageService2"


def test_viewer_manifest_normalizes_p2_object_label(cfg, p2_manifest):
    """P2 object labels ({"@value", "@language"}) are not P3 language maps —
    passing them through unchanged corrupts the published manifest."""
    canvas = p2_manifest["sequences"][0]["canvases"][0]
    canvas["label"] = {"@value": "f. 1r", "@language": "sv"}
    p2_manifest["label"] = {"@value": "P2 vol"}  # no @language -> "none"
    m = _p2_viewer_manifest(cfg, p2_manifest)
    assert m["items"][0]["label"] == {"sv": ["f. 1r"]}
    assert m["label"] == {"none": ["P2 vol"]}


def test_viewer_manifest_normalizes_p2_array_of_strings_label(cfg, p2_manifest):
    canvas = p2_manifest["sequences"][0]["canvases"][0]
    canvas["label"] = ["f. 1r", "first leaf"]
    m = _p2_viewer_manifest(cfg, p2_manifest)
    assert m["items"][0]["label"] == {"none": ["f. 1r", "first leaf"]}


def test_viewer_manifest_normalizes_p2_array_of_objects_label(cfg, p2_manifest):
    canvas = p2_manifest["sequences"][0]["canvases"][0]
    canvas["label"] = [
        {"@value": "f. 1r", "@language": "en"},
        {"@value": "blad 1", "@language": "sv"},
        {"@value": "fol. 1", "@language": "sv"},
    ]
    m = _p2_viewer_manifest(cfg, p2_manifest)
    assert m["items"][0]["label"] == {"en": ["f. 1r"], "sv": ["blad 1", "fol. 1"]}


def test_viewer_manifest_label_falls_back_when_empty(cfg, p2_manifest):
    p2_manifest["sequences"][0]["canvases"][0]["label"] = []
    del p2_manifest["label"]
    m = _p2_viewer_manifest(cfg, p2_manifest)
    assert m["items"][0]["label"] == {"none": ["0001"]}
    assert m["label"] == {"none": ["SE-RA-1234"]}


def test_a_scored_canvas_says_its_predicted_quality(cfg):
    from htrflow_batch.iiif import PageRef

    pages = [
        PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})
    ]
    m = build_viewer_manifest(
        cfg, {}, pages, {"0001": (10, 10)}, quality={"0001": 0.87312}
    )
    assert m["items"][0]["metadata"] == [
        {"label": {"en": ["Predicted quality"]}, "value": {"none": ["0.87"]}}
    ]
    assert "metadata" not in m


def test_the_volume_summary_is_manifest_metadata(cfg):
    from htrflow_batch.iiif import PageRef

    pages = [
        PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})
    ]
    summary = {"mean": 0.8123, "min": 0.4, "scored": 12}
    m = build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)}, summary=summary)
    assert m["metadata"] == [
        {
            "label": {"en": ["Predicted quality"]},
            "value": {"none": ["mean 0.81, lowest 0.40, over 12 pages"]},
        }
    ]


def test_without_scores_the_viewer_manifest_is_unchanged(cfg):
    from htrflow_batch.iiif import PageRef

    pages = [
        PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})
    ]
    plain = build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)})
    assert plain == build_viewer_manifest(
        cfg, {}, pages, {"0001": (10, 10)}, quality={}, summary=None
    )
    assert "metadata" not in plain and "metadata" not in plain["items"][0]


def test_viewer_manifest_publishes_no_javascript_body(cfg):
    """W6: end to end -- nothing a canvas carries reaches the published
    manifest without having been scheme-checked first."""
    from htrflow_batch.iiif import PageRef
    from htrflow_batch.viewer import build_viewer_manifest

    canvas = {
        "items": [
            {
                "items": [
                    {
                        "body": {
                            "id": "javascript:alert(1)",
                            "service": [{"id": "https://img/iiif/page-1"}],
                        }
                    }
                ]
            }
        ]
    }
    page = PageRef(
        index=1,
        name="0001",
        image_url="https://img/iiif/page-1/full/max/0/default.jpg",
        canvas=canvas,
    )
    manifest = build_viewer_manifest(cfg, {}, [page], {"0001": (100, 200)})
    assert "javascript:" not in json.dumps(manifest)


def _one_page(source, quality=None, summary=None, cfg=None):
    from htrflow_batch.iiif import PageRef

    canvas = (source.get("items") or [{}])[0]
    pages = [PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas=canvas)]
    return build_viewer_manifest(
        cfg, source, pages, {"0001": (10, 10)}, quality=quality, summary=summary
    )


P3_RIGHTS = {
    "requiredStatement": {
        "label": {"en": ["Attribution"]},
        "value": {"en": ["Provided by an archive"]},
    },
    "rights": "http://creativecommons.org/publicdomain/mark/1.0/",
    "summary": {"en": ["A court book"]},
    "metadata": [{"label": {"en": ["Date"]}, "value": {"none": ["1652"]}}],
    "navDate": "1652-01-01T00:00:00Z",
    "behavior": ["paged"],
    "viewingDirection": "right-to-left",
}


def test_the_source_rights_and_reading_hints_are_published(cfg):
    """Clients MUST render requiredStatement: dropping it republished an
    archive's images under our domain without its credit line. behavior and
    viewingDirection tell a viewer a book from a stack of sheets."""
    m = _one_page(dict(P3_RIGHTS, items=[{"id": "c1"}]), cfg=cfg)
    for key, value in P3_RIGHTS.items():
        assert m[key] == value


def test_the_quality_summary_follows_the_source_metadata(cfg):
    summary = {"mean": 0.8, "min": 0.4, "scored": 1}
    m = _one_page(dict(P3_RIGHTS, items=[{"id": "c1"}]), summary=summary, cfg=cfg)
    assert m["metadata"][0] == P3_RIGHTS["metadata"][0]
    assert m["metadata"][1]["label"] == {"en": ["Predicted quality"]}


def test_p2_rights_and_reading_hints_are_published_in_p3_form(cfg, p2_manifest):
    p2_manifest.update(
        attribution="Provided by an archive",
        license="http://creativecommons.org/publicdomain/mark/1.0/",
        description={"@value": "En dombok", "@language": "sv"},
        metadata=[{"label": "Date", "value": "1652"}],
        viewingHint="paged",
        viewingDirection="right-to-left",
    )
    m = _p2_viewer_manifest(cfg, p2_manifest)
    assert m["requiredStatement"] == {
        "label": {"en": ["Attribution"]},
        "value": {"none": ["Provided by an archive"]},
    }
    assert m["rights"] == "http://creativecommons.org/publicdomain/mark/1.0/"
    assert m["summary"] == {"sv": ["En dombok"]}
    assert m["metadata"] == [{"label": {"none": ["Date"]}, "value": {"none": ["1652"]}}]
    assert m["behavior"] == ["paged"]
    assert m["viewingDirection"] == "right-to-left"


@pytest.mark.parametrize(
    "canvas",
    [
        {"id": "c1", "behavior": ["non-paged"]},
        {"@id": "c1", "viewingHint": "non-paged"},
    ],
)
def test_a_non_paged_canvas_stays_out_of_the_page_turning(cfg, canvas):
    """A colour chart or a loose slip is marked non-paged; a viewer MUST NOT
    put it in the page-turning view."""
    m = _one_page({"items": [canvas]}, cfg=cfg)
    assert m["items"][0]["behavior"] == ["non-paged"]


def test_a_rights_value_that_is_not_a_web_uri_is_left_out(cfg):
    """`rights` is a URI a viewer may link to; anything else is not one."""
    m = _one_page({"rights": "javascript:alert(1)", "items": [{"id": "c1"}]}, cfg=cfg)
    assert "rights" not in m


def test_the_alto_link_uses_the_registered_profile_and_a_real_media_type(cfg):
    """The IIIF registry of profiles names ALTO
    `http://www.loc.gov/standards/alto`; `application/xml+alto` is no
    registered media type. The viewer only needs "alto" in the profile of
    the first seeAlso, which this keeps."""
    sa = _one_page({"items": [{"id": "c1"}]}, cfg=cfg)["items"][0]["seeAlso"]
    assert sa[0]["profile"] == "http://www.loc.gov/standards/alto"
    assert sa[0]["format"] == "application/xml"
