"""The UV4 patch's text panel reads the page's predicted quality off the
ALTO it already fetched, and inserts it as text (the patch's XSS rule)."""

from pathlib import Path

PATCH = Path(__file__).resolve().parents[3] / ".docker" / "uv4-uv-html.patch"


def _added(text: str) -> str:
    return "\n".join(
        line[1:]
        for line in text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def test_the_text_panel_reads_page_pc_and_sets_it_as_text():
    added = _added(PATCH.read_text())
    assert 'getAttribute("PC")' in added
    block = added[added.index('getAttribute("PC")') :][:600]
    assert ".text(" in block
    assert ".html(" not in block
    assert "Predicted quality" in block


def test_the_preamble_says_what_the_quality_hunk_does():
    preamble = PATCH.read_text().split("diff --git", 1)[0]
    assert "predicted quality" in preamble.lower()


def _file_added(name: str) -> str:
    """The added lines of one file's section of the patch."""
    text = PATCH.read_text()
    start = text.index("diff --git a/" + name)
    end = text.find("\ndiff --git ", start + 1)
    return _added(text[start : end if end >= 0 else len(text)])


PANEL = "src/content-handlers/iiif/modules/uv-textrightpanel-module/TextRightPanel.ts"
STYLES = (
    "src/content-handlers/iiif/modules/"
    "uv-openseadragoncenterpanel-module/css/styles.less"
)


def test_the_text_panel_reads_each_lines_polygon_points():
    added = _file_added(PANEL)
    assert '"Polygon"' in added
    assert '"POINTS"' in added


def test_the_polygon_is_built_as_svg_nodes_never_as_markup():
    added = _file_added(PANEL)
    assert 'createElementNS(SVG_NS, "svg")' in added
    assert 'createElementNS(SVG_NS, "polygon")' in added
    assert 'SVG_NS = "http://www.w3.org/2000/svg"' in added
    # No markup string carries the points: no HTML setter, no svg literal.
    for marker in (".html(", "innerHTML", "outerHTML", "<svg", "<polygon"):
        assert marker not in added, marker


def test_the_points_are_parsed_as_finite_numbers_or_refused():
    added = _file_added(PANEL)
    parser = added[added.index("parseAltoPolygon") :][:1500]
    assert "Number.isFinite" in parser
    assert "return null" in parser
    # An odd count or fewer than three points is no outline.
    assert "% 2" in parser
    assert "< 6" in parser


def test_a_line_without_a_usable_polygon_keeps_its_box():
    added = _file_added(PANEL)
    # The svg (and the class that moves the outline onto it) is added only
    # when the parse gave points; otherwise the div is left exactly as before.
    at = added.index('addClass("hasPolygon")')
    guard = added[:at].rsplit("if (", 1)[1]
    assert guard.lstrip().startswith("outline")
    assert "bounding box" in added  # the hit-area limitation is written down


def test_the_styles_move_the_outline_onto_the_polygon():
    added = _file_added(STYLES)
    assert "&.hasPolygon" in added
    assert "vector-effect: non-scaling-stroke" in added
    # Clicks go to the overlay div, whose id the handlers read.
    assert "pointer-events: none" in added
    assert "fill:" in added and "stroke:" in added


def test_the_preamble_says_the_lines_draw_their_polygons():
    preamble = PATCH.read_text().split("diff --git", 1)[0]
    bullets = preamble.split("\n- ")
    panel = [b for b in bullets if b.startswith("TextRightPanel.ts")]
    assert any("polygon" in b.lower() for b in panel)
    styles = preamble[preamble.index("- styles.less:") :]
    assert "polygon" in styles.lower()
