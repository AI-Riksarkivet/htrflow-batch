from pathlib import Path

import pytest
from htrflow_batch.config import Config
from htrflow_batch.iiif import pages_from_manifest
from htrflow_batch.viewer import parse_alto_dims, build_viewer_manifest

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="2500" HEIGHT="3538" ID="p1"/></Layout>
</alto>"""


def test_parse_alto_dims(tmp_path):
    p = tmp_path / "0001.xml"
    p.write_text(ALTO)
    assert parse_alto_dims(p) == (2500, 3538)


def test_parse_alto_dims_missing(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<alto/>")
    with pytest.raises(ValueError):
        parse_alto_dims(p)


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
    assert parse_alto_dims(p) == (2500, 3538)


def test_build_viewer_manifest(sample_manifest, cfg):
    pages = pages_from_manifest(sample_manifest, width=2500)
    dims = {"0001": (2500, 3538), "0002": (2500, 3520)}  # page 3 unprocessed
    m = build_viewer_manifest(cfg, sample_manifest, pages, dims)
    assert m["type"] == "Manifest"
    assert m["id"] == "http://public/htr-results/demo-v1/SE-RA-1234/iiif.json"
    assert m["label"] == sample_manifest["label"]
    assert len(m["items"]) == 2                      # only processed pages
    c1 = m["items"][0]
    assert (c1["width"], c1["height"]) == (2500, 3538)   # capped dims (D19)
    body = c1["items"][0]["items"][0]["body"]
    assert body["service"][0]["id"].endswith("page-00001")
    sa = c1["seeAlso"][0]
    assert sa["id"] == \
        "http://public/htr-results/demo-v1/SE-RA-1234/alto/0001.xml"
    assert "alto" in sa["profile"]


def test_viewer_manifest_declares_search_service(sample_manifest, cfg):
    """UV4 (RA fork) gates the ALTO text panel on getSearchService() —
    without a search service entry the transcription panel never shows."""
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages,
                              {"0001": (2500, 3538)})
    svc = m["service"][0]
    assert svc["profile"] == "http://iiif.io/api/search/1/search"
    assert svc["@id"] == \
        "http://public/htr-results/demo-v1/SE-RA-1234/search"


def test_canvas_thumbnail_from_image_service(sample_manifest, cfg):
    """With a IIIF image service, thumbnails use a sized request (width
    syntax — lbiiif 501s on !w,h)."""
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages,
                              {"0001": (2500, 3538)})
    thumb = m["items"][0]["thumbnail"][0]
    assert thumb["id"] == \
        "https://iiif.example/mock-vol/page-00001/full/200,/0/default.jpg"
    assert thumb["type"] == "Image"


def test_canvas_thumbnail_falls_back_to_static_image(sample_manifest, cfg):
    """Without an image service (mocked IIIF), the full image doubles as
    thumbnail — UV renders nothing at all if the property is absent."""
    for canvas in sample_manifest["items"]:
        body = canvas["items"][0]["items"][0]["body"]
        del body["service"]
    pages = pages_from_manifest(sample_manifest, width=2500)
    m = build_viewer_manifest(cfg, sample_manifest, pages,
                              {"0001": (2500, 3538)})
    thumb = m["items"][0]["thumbnail"][0]
    assert thumb["id"] == \
        "https://iiif.example/mock-vol/page-00001/full/max/0/default.jpg"
    assert (thumb["width"], thumb["height"]) == (2500, 3538)
