"""ALTO rewriting for `htrflow-campaigns export`: scale, or build an empty page."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from htrflow_converter.export_alto import ALTO_NS, AltoMismatch, empty_alto, scale_alto

FIXTURE = Path(__file__).parent / "fixtures" / "export" / "alto-2500.xml"


def test_empty_alto_is_the_spec_example():
    xml = empty_alto((3494, 2472), "R0001203_00001", 1)
    root = ET.fromstring(xml)
    assert [e.tag.split("}")[1] for e in root.find(f"{{{ALTO_NS}}}Description")] == [
        "MeasurementUnit",
        "sourceImageInformation",
    ]
    page = root.find(f".//{{{ALTO_NS}}}Page")
    assert page.attrib == {
        "WIDTH": "3494",
        "HEIGHT": "2472",
        "PHYSICAL_IMG_NR": "1",
        "ID": "_00001",
    }
    ps = page.find(f"{{{ALTO_NS}}}PrintSpace")
    assert ps.attrib == {"HPOS": "0", "VPOS": "0", "WIDTH": "3494", "HEIGHT": "2472"}
    assert len(ps) == 0
    assert root.find(f".//{{{ALTO_NS}}}fileName").text == "R0001203_00001"
    assert root.find(f".//{{{ALTO_NS}}}ReadingOrder") is None
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert b"xsi:schemaLocation" in xml and b"alto-4-4.xsd" in xml
    assert b"ns0:" not in xml


def test_scaling_reaches_the_native_size_on_every_coordinate():
    xml = scale_alto(FIXTURE.read_bytes(), (3494, 2472), "R0001203_00001", 1)
    assert b"ns0:" not in xml and b"xsi:schemaLocation" in xml
    out = ET.fromstring(xml)
    page = out.find(f".//{{{ALTO_NS}}}Page")
    assert (page.get("WIDTH"), page.get("HEIGHT"), page.get("PHYSICAL_IMG_NR")) == (
        "3494",
        "2472",
        "1",
    )
    assert page.get("PC") == "0.87"
    # the fixture's own ID is "_0001", the run's page name: the published
    # page carries the image number in five digits, as its fileName does
    assert page.get("ID") == "_00001"
    s = out.find(f".//{{{ALTO_NS}}}String")
    assert int(s.get("HPOS")) == round(100 * 3494 / 2500)
    assert int(s.get("VPOS")) == round(200 * 2472 / 1769)
    assert int(s.get("WIDTH")) == round(300 * 3494 / 2500)
    assert int(s.get("HEIGHT")) == round(50 * 2472 / 1769)
    assert out.find(f".//{{{ALTO_NS}}}fileName").text == "R0001203_00001"
    assert out.find(f".//{{{ALTO_NS}}}Processing") is not None
    assert out.find(f".//{{{ALTO_NS}}}PrintSpace").attrib == {}


def test_both_points_spellings_scale_and_keep_their_spelling():
    out = scale_alto(FIXTURE.read_bytes(), (5000, 3538), "X_00001", 1)  # exactly x2
    comma, space = [
        p.get("POINTS") for p in ET.fromstring(out).iter(f"{{{ALTO_NS}}}Polygon")
    ]
    assert comma == "200,400 800,400 800,500 200,500"
    assert space == "200 400 800 400 800 500 200 500"


def test_a_page_already_native_comes_out_unchanged_but_named():
    out = ET.fromstring(scale_alto(FIXTURE.read_bytes(), (2500, 1769), "X_00001", 1))
    assert out.find(f".//{{{ALTO_NS}}}String").get("HPOS") == "100"
    assert out.find(f".//{{{ALTO_NS}}}fileName").text == "X_00001"


def test_a_different_shape_is_refused():
    with pytest.raises(AltoMismatch):
        scale_alto(FIXTURE.read_bytes(), (2472, 3494), "X_00001", 1)  # rotated


def test_a_malformed_alto_is_a_mismatch_not_a_parse_crash():
    with pytest.raises(AltoMismatch, match="not well-formed"):
        scale_alto(b"<alto", (2500, 1769), "V0000001_00002", 2)


def _without(path: str) -> bytes:
    """The fixture with the element at ``path`` (from the root) removed."""
    root = ET.fromstring(FIXTURE.read_bytes())
    *parent_path, name = path.split("/")
    parent = root.find("/".join(f"{{{ALTO_NS}}}{p}" for p in parent_path) or ".")
    parent.remove(parent.find(f"{{{ALTO_NS}}}{name}"))
    return ET.tostring(root)


def _children(el) -> list[str]:
    return [c.tag.split("}")[1] for c in el]


@pytest.mark.parametrize(
    ("removed", "description"),
    [
        (
            "Description/sourceImageInformation/fileName",
            ["MeasurementUnit", "sourceImageInformation", "Processing", "Processing"],
        ),
        (
            "Description/sourceImageInformation",
            ["MeasurementUnit", "sourceImageInformation", "Processing", "Processing"],
        ),
        ("Description", ["MeasurementUnit", "sourceImageInformation"]),
    ],
)
def test_a_missing_file_name_is_created_in_alto_order(removed, description):
    out = ET.fromstring(scale_alto(_without(removed), (3494, 2472), "X_00001", 1))
    assert _children(out)[0] == "Description"
    desc = out.find(f"{{{ALTO_NS}}}Description")
    assert _children(desc) == description
    assert desc.find(f"{{{ALTO_NS}}}MeasurementUnit").text == "pixel"
    info = desc.find(f"{{{ALTO_NS}}}sourceImageInformation")
    assert _children(info)[0] == "fileName"
    assert info.find(f"{{{ALTO_NS}}}fileName").text == "X_00001"


# htrflow's ALTO template counts a word's box from its block's corner instead
# of the page's (seen at htrflow 34ebd90). The fixture is a document from
# htrflow's own serializer, with the template as it is.
WORDS_FROM_BLOCK = FIXTURE.with_name("alto-words-from-block.xml")
SAME_SIZE = (2500, 1769)


def _boxes(xml: bytes, tag: str) -> dict[str, tuple[int, ...]]:
    return {
        el.get("ID"): tuple(int(el.get(k)) for k in ("HPOS", "VPOS", "WIDTH", "HEIGHT"))
        for el in ET.fromstring(xml).iter(f"{{{ALTO_NS}}}{tag}")
    }


def test_words_counted_from_their_block_are_published_on_the_page():
    out = scale_alto(WORDS_FROM_BLOCK.read_bytes(), SAME_SIZE, "X_00001", 1)
    assert _boxes(out, "String") == {
        "block0_line0_word0": (350, 300, 300, 70),
        "block0_line0_word1": (670, 310, 480, 60),
        "block0_line1_word0": (350, 400, 200, 60),
        "block0_line1_word1": (560, 400, 440, 60),
        "block1_line0_word0": (1420, 280, 400, 60),
        "block1_line0_word1": (1830, 285, 390, 55),
    }


def _page(blocks: str) -> bytes:
    return (
        f'<alto xmlns="{ALTO_NS}"><Layout><Page WIDTH="1000" HEIGHT="1000">'
        f"<PrintSpace>{blocks}</PrintSpace></Page></Layout></alto>"
    ).encode()


def test_words_that_fit_their_lines_both_ways_are_left_as_written():
    """A block two pixels from the page's corner, a word with room around it:
    nothing says which form this is, so nothing is moved."""
    xml = _page(
        '<TextBlock ID="b" HPOS="2" VPOS="2" WIDTH="900" HEIGHT="100">'
        '<TextLine ID="l" HPOS="10" VPOS="10" WIDTH="800" HEIGHT="80">'
        '<String ID="w" HPOS="100" VPOS="20" WIDTH="50" HEIGHT="40" CONTENT="x"/>'
        "</TextLine></TextBlock>"
    )
    out = scale_alto(xml, (1000, 1000), "X_00001", 1)
    assert _boxes(out, "String") == {"w": (100, 20, 50, 40)}


def test_a_block_at_the_pages_corner_decides_nothing_for_the_others():
    """Its words fit both ways, so they take no side; the second block's
    words are in their line only once moved, and that settles the page."""
    xml = _page(
        '<TextBlock ID="a" HPOS="0" VPOS="0" WIDTH="400" HEIGHT="100">'
        '<TextLine ID="la" HPOS="0" VPOS="0" WIDTH="400" HEIGHT="50">'
        '<String ID="wa1" HPOS="0" VPOS="0" WIDTH="100" HEIGHT="50" CONTENT="a"/>'
        '<String ID="wa2" HPOS="110" VPOS="0" WIDTH="100" HEIGHT="50" CONTENT="b"/>'
        "</TextLine></TextBlock>"
        '<TextBlock ID="b" HPOS="500" VPOS="600" WIDTH="400" HEIGHT="100">'
        '<TextLine ID="lb" HPOS="500" VPOS="600" WIDTH="400" HEIGHT="50">'
        '<String ID="wb" HPOS="0" VPOS="0" WIDTH="100" HEIGHT="50" CONTENT="c"/>'
        "</TextLine></TextBlock>"
    )
    out = scale_alto(xml, (1000, 1000), "X_00001", 1)
    assert _boxes(out, "String") == {
        "wa1": (0, 0, 100, 50),
        "wa2": (110, 0, 100, 50),
        "wb": (500, 600, 100, 50),
    }


def test_a_line_of_text_without_word_boxes_is_published_as_it_is():
    xml = _page(
        '<TextBlock ID="b" HPOS="100" VPOS="100" WIDTH="300" HEIGHT="300">'
        '<TextLine ID="l" HPOS="120" VPOS="130" WIDTH="200" HEIGHT="40">'
        '<String CONTENT="a whole line"/>'
        "</TextLine></TextBlock>"
    )
    out = ET.fromstring(scale_alto(xml, (1000, 1000), "X_00001", 1))
    assert out.find(f".//{{{ALTO_NS}}}String").attrib == {"CONTENT": "a whole line"}
    assert _boxes(ET.tostring(out), "TextLine") == {"l": (120, 130, 200, 40)}


def test_a_block_near_the_pages_edge_still_has_its_words_moved():
    """The block is 40 px down, less than half a line: the word's middle is
    in its line either way, but the word as a whole only once it is moved."""
    xml = _page(
        '<TextBlock ID="b" HPOS="0" VPOS="40" WIDTH="800" HEIGHT="300">'
        '<TextLine ID="l" HPOS="0" VPOS="60" WIDTH="800" HEIGHT="100">'
        '<String ID="w" HPOS="10" VPOS="20" WIDTH="200" HEIGHT="100" CONTENT="x"/>'
        "</TextLine></TextBlock>"
    )
    out = scale_alto(xml, (1000, 1000), "X_00001", 1)
    assert _boxes(out, "String") == {"w": (10, 60, 200, 100)}
