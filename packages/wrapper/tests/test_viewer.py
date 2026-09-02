import pytest

from htrflow_batch.iiif import pages_from_manifest
from htrflow_batch.viewer import build_viewer_manifest, parse_alto_dims_bytes

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="2500" HEIGHT="3538" ID="p1"/></Layout>
</alto>"""


def test_parse_alto_dims(tmp_path):
    p = tmp_path / "0001.xml"
    p.write_text(ALTO)
    assert parse_alto_dims_bytes(p.read_bytes()) == (2500, 3538)


def test_parse_alto_dims_missing(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<alto/>")
    with pytest.raises(ValueError):
        parse_alto_dims_bytes(p.read_bytes())


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
    assert parse_alto_dims_bytes(p.read_bytes()) == (2500, 3538)


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


def test_canvas_has_no_thumbnail_key(sample_manifest, cfg):
    """B63/D7: canvas thumbnails were dropped along with the campaign
    browser's per-volume thumbnail — the viewer manifest must not carry one."""
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages, {"0001": (2500, 3538)})
    assert "thumbnail" not in m["items"][0]


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
