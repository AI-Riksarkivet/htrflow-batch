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
