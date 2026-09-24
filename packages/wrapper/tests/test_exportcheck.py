"""The export check: a page whose ALTO or PAGE XML holds none of the text
htrflow recognized for it fails instead of publishing empty (docs: wrapper,
stages).

The trees are shaped like htrflow's own (``Document``/``Region``: a node's
children in ``regions``, its text in ``transcription[0].text``) and the XML
like what htrflow's ALTO and PAGE templates write for them -- the real
serializers are pinned in test_driver_real.py."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from htrflow_batch.exportcheck import NOT_EXPORTED, TextNotExported, check_export


def node(*children, text: str | None = None):
    transcription = [SimpleNamespace(text=text, confidence=0.9)] if text else []
    return SimpleNamespace(regions=list(children), transcription=transcription)


ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Layout><Page><PrintSpace>
{blocks}
</PrintSpace></Page></Layout></alto>"""

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15">
<Page>{regions}</Page></PcGts>"""


def alto_block(*lines: str) -> str:
    inner = "".join(f'<TextLine><String CONTENT="{t}" /></TextLine>' for t in lines)
    return f"<TextBlock>{inner}</TextBlock>"


def page_region(*lines: str) -> str:
    inner = "".join(
        f"<TextLine><TextEquiv><Unicode>{t}</Unicode></TextEquiv></TextLine>"
        for t in lines
    )
    return f"<TextRegion>{inner}</TextRegion>"


def write(tmp_path: Path, alto: str, page: str) -> dict[str, Path]:
    files = {"alto": tmp_path / "alto.xml", "page": tmp_path / "page.xml"}
    files["alto"].write_text(alto)
    files["page"].write_text(page)
    return files


def flat_export(tmp_path: Path, regions: int) -> dict[str, Path]:
    """What htrflow writes for lines directly on the page: ALTO skips a
    region with no children, PAGE writes it as an empty TextRegion."""
    return write(
        tmp_path,
        ALTO.format(blocks=""),
        PAGE.format(regions="<TextRegion></TextRegion>" * regions),
    )


def test_lines_directly_on_the_page_fail_the_page_with_the_structural_cause(
    tmp_path,
):
    tree = node(node(text="Anno 1723"), node(text="den 4 Maji"), node(text="Item"))
    with pytest.raises(TextNotExported) as caught:
        check_export(tree, flat_export(tmp_path, 3))
    said = str(caught.value)
    assert said.startswith(NOT_EXPORTED)
    assert "htrflow recognized 3 lines" in said
    assert "ALTO and PAGE XML hold none of them" in said
    assert "directly on the page" in said
    assert "add a region Segmentation step before the line step" in said


def test_a_blank_page_is_ok_with_its_empty_export(tmp_path):
    """Nothing recognized, nothing to lose: a blank page still publishes its
    empty-but-valid ALTO."""
    check_export(node(), flat_export(tmp_path, 0))
    # a line segmented but read as nothing is not recognized text either
    check_export(node(node(node(text=" "))), flat_export(tmp_path, 1))


def test_region_then_line_text_is_ok(tmp_path):
    tree = node(node(node(text="Anno 1723"), node(text="den 4 Maji")))
    files = write(
        tmp_path,
        ALTO.format(blocks=alto_block("Anno 1723", "den 4 Maji")),
        PAGE.format(regions=page_region("Anno 1723", "den 4 Maji")),
    )
    check_export(tree, files)


def test_word_level_text_counts_the_words_htrflow_writes(tmp_path):
    """Word-level recognition gives a line its text AND word children with
    theirs; the templates write the words, so the words are what is held."""
    line = node(node(text="Anno"), node(text="1723"), text="Anno 1723")
    files = write(
        tmp_path,
        ALTO.format(
            blocks='<TextBlock><TextLine><String CONTENT="Anno" />'
            '<String CONTENT="1723" /></TextLine></TextBlock>'
        ),
        PAGE.format(
            regions="<TextRegion><TextLine>"
            "<Word><TextEquiv><Unicode>Anno</Unicode></TextEquiv></Word>"
            "<Word><TextEquiv><Unicode>1723</Unicode></TextEquiv></Word>"
            "</TextLine></TextRegion>"
        ),
    )
    check_export(node(node(line)), files)


def test_markup_in_the_text_is_compared_as_text(tmp_path):
    tree = node(node(node(text='a & b < "c"')))
    files = write(
        tmp_path,
        ALTO.format(blocks=alto_block("a &amp; b &lt; &quot;c&quot;")),
        PAGE.format(regions=page_region('a &amp; b &lt; "c"')),
    )
    check_export(tree, files)


def test_a_format_that_holds_none_is_named_alone(tmp_path):
    tree = node(node(node(text="Anno 1723")))
    files = write(
        tmp_path,
        ALTO.format(blocks=alto_block("Anno 1723")),
        PAGE.format(regions="<TextRegion></TextRegion>"),
    )
    with pytest.raises(TextNotExported, match="the PAGE XML holds none of it"):
        check_export(tree, files)


def test_another_nesting_names_the_general_rule(tmp_path):
    """Four segmentation levels put the text below what the templates write:
    the cause is not the flat one, so the sentence does not claim it is."""
    deep = node(node(node(node(node(text="Anno 1723")))))
    files = write(
        tmp_path,
        ALTO.format(blocks="<TextBlock><TextLine></TextLine></TextBlock>"),
        PAGE.format(regions="<TextRegion><TextLine></TextLine></TextRegion>"),
    )
    with pytest.raises(TextNotExported) as caught:
        check_export(deep, files)
    said = str(caught.value)
    assert "directly on the page" not in said
    assert "inside a region" in said


def test_a_partial_loss_is_logged_not_failed(tmp_path, caplog):
    """A region the line model found no line in is read whole and sits at
    depth one, where the templates write no text. The page keeps the rest of
    its text; the loss is said in the run log."""
    tree = node(node(node(text="Anno 1723")), node(text="marginal note"))
    files = write(
        tmp_path,
        ALTO.format(blocks=alto_block("Anno 1723")),
        PAGE.format(regions=page_region("Anno 1723") + "<TextRegion></TextRegion>"),
    )
    with caplog.at_level(logging.WARNING, logger="htrflow_batch"):
        check_export(tree, files)
    assert "htrflow recognized 2 lines" in caplog.text
    assert "the ALTO holds 1" in caplog.text


def test_an_unparseable_file_is_left_to_the_upload_check(tmp_path):
    """store.upload_page refuses malformed XML with its own sentence; this
    check does not replace it with a different one."""
    tree = node(node(node(text="Anno 1723")))
    files = write(tmp_path, "<alto><unclosed>", PAGE.format(regions=""))
    with pytest.raises(TextNotExported, match="the PAGE XML holds none of it"):
        check_export(tree, files)
    files = write(
        tmp_path, "<alto><unclosed>", PAGE.format(regions=page_region("Anno 1723"))
    )
    check_export(tree, files)


def test_a_tree_that_is_not_htrflows_is_not_judged(tmp_path):
    """No ``regions``/``transcription`` to read: nothing counts as recognized."""
    check_export(object(), flat_export(tmp_path, 0))
    check_export(None, flat_export(tmp_path, 0))
