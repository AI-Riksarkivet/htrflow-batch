import xml.etree.ElementTree as ET

import pytest

from htrflow_batch import safexml

ALTO = b"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="2500" HEIGHT="3538" ID="p1"/></Layout>
</alto>"""

# A billion-laughs document: ten levels of ten-fold entity expansion.
LAUGHS = (
    b'<?xml version="1.0"?>\n<!DOCTYPE lolz [\n<!ENTITY lol "lol">\n'
    + b"".join(
        b'<!ENTITY lol%d "%s">\n'
        % (
            i,
            b"".join(
                b"&lol%s;" % (b"" if i == 1 else b"%d" % (i - 1)) for _ in range(10)
            ),
        )
        for i in range(1, 10)
    )
    + b"]>\n<alto>&lol9;</alto>"
)
EXTERNAL = (
    b'<?xml version="1.0"?>\n<!DOCTYPE alto [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
    b"<alto>&x;</alto>"
)
DOCTYPE_ONLY = b'<?xml version="1.0"?>\n<!DOCTYPE alto>\n<alto/>'


def test_an_ordinary_alto_parses(tmp_path):
    root = safexml.fromstring(ALTO)
    assert root.tag.endswith("alto")
    path = tmp_path / "0001.xml"
    path.write_bytes(ALTO)
    assert safexml.parse(path).getroot().tag == root.tag


@pytest.mark.parametrize(
    "doc", [LAUGHS, EXTERNAL, DOCTYPE_ONLY], ids=["laughs", "external", "doctype"]
)
def test_a_dtd_is_refused_as_a_parse_error(tmp_path, doc):
    """ALTO and PAGE never carry a DTD; refusing every one closes entity
    expansion and external entities whatever expat the image ships, and as a
    ParseError it lands in each caller's existing 'not well-formed' path."""
    with pytest.raises(ET.ParseError, match="DTD"):
        safexml.fromstring(doc)
    path = tmp_path / "0001.xml"
    path.write_bytes(doc)
    with pytest.raises(ET.ParseError, match="DTD"):
        safexml.parse(path)


def test_malformed_xml_is_still_the_stdlib_parse_error():
    with pytest.raises(ET.ParseError):
        safexml.fromstring(b"<alto><Layout></alto>")
