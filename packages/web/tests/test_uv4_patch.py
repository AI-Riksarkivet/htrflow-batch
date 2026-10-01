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


# --- the viewer's login gate (spec §7) -------------------------------------

#: uv.html as the fork ships it at UV4_REF, byte for byte; the frontend's
#: uv-gate.test.ts runs the patched page's script against it.
UPSTREAM_UV = PATCH.parents[1] / "frontend" / "src" / "lib" / "fixtures" / "uv4-uv.html"


def _patched_uv(tmp_path):
    """uv.html as the web image builds it: the fork's page with the patch
    applied by ``git apply``, the same tool the Dockerfile uses."""
    import shutil
    import subprocess

    (tmp_path / "src").mkdir()
    shutil.copyfile(UPSTREAM_UV, tmp_path / "src" / "uv.html")
    subprocess.run(
        ["git", "apply", "--include=src/uv.html", str(PATCH)],
        cwd=tmp_path,
        check=True,
    )
    return (tmp_path / "src" / "uv.html").read_text()


def test_the_gate_asks_for_a_results_manifest_before_uv_loads_it():
    added = _file_added("src/uv.html")
    gate = added[added.index("function gate") :]
    assert '"/results/"' in gate and "location.origin" in gate
    assert 'credentials: "same-origin"' in gate
    # GET, not HEAD: a HEAD error has no body, and the proxy then cannot
    # tell a revoked login (401) from a refusal (403).
    assert '"HEAD"' not in added
    assert "status === 401" in gate and "status === 403" in gate
    assert "Your account may not read this volume." in added
    assert '"/login?next=" + encodeURIComponent(' in added
    # Built as nodes: no markup string carries the URL into the page.
    for marker in (".html(", "innerHTML", "outerHTML"):
        assert marker not in added, marker
    # UV starts only through the gate.
    assert added.count('UV.init("uv", data)') == 1
    assert "gate(data.iiifManifestId, start)" in added


def test_the_patched_viewer_runs_its_gate_under_its_hashed_policy(tmp_path):
    """The gate is inline script in uv.html, so it runs only because
    uv_csp hashes every inline block of the page it serves. No file is
    added beside it either: the image's site step refuses a reference UV's
    build did not produce."""
    import base64
    import hashlib

    from htrflow_web.app import _page, uv_csp

    page = _patched_uv(tmp_path)
    bodies = _page(page).bodies
    assert len(bodies) == 1 and "function gate" in bodies[0]
    digest = base64.b64encode(hashlib.sha256(bodies[0].encode()).digest()).decode()
    csp = uv_csp(tmp_path / "src")
    assert csp is not None and f"'sha256-{digest}'" in csp
    assert "src=" not in _file_added("src/uv.html")


def test_the_preamble_says_what_the_gate_does():
    preamble = PATCH.read_text().split("diff --git", 1)[0]
    assert "/results/" in preamble and "401" in preamble and "403" in preamble
