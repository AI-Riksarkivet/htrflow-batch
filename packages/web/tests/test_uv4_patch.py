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
