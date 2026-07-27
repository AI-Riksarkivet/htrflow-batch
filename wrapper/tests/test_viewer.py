from pathlib import Path

import pytest
from htrflow_batch.viewer import parse_alto_dims

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
