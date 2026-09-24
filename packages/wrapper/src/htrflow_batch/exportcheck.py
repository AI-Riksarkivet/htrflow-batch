"""Does the export hold the text htrflow recognized? (docs: wrapper, stages)

htrflow's ALTO and PAGE templates write a line's text only for a line inside
a region -- page, region, line, and the words of such a line (htrflow
``serialization/templates/alto-4-4`` and ``page2019``). A pipeline whose
lines sit directly on the page gets every line written as an empty region:
the file is valid, the page looks done, and all of its text is gone. At
archive scale that is a volume published without a word in it and a
campaign that says Succeeded.

So after htrflow exports a page, the text it recognized -- read off the
page's tree, where it is in memory anyway -- is looked for in the files. A
page whose export holds none of it fails; the cause is the pipeline's shape,
so the same page fails the same way on every retry and the sentence says
what to change. Where only some of it is missing -- a region the line model
found no line in is read whole, one level too high to be written -- the
page keeps the rest and the loss is said in the run log.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

log = logging.getLogger("htrflow_batch")

#: How every page failure of this kind starts: ``main._verify`` knows a
#: volume whose every page failed this way by it.
NOT_EXPORTED = "recognized text not exported"

#: The formats, as a sentence names them.
_NAMES = {"alto": "ALTO", "page": "PAGE XML"}

_FIX = "(docs: reference/campaign-yaml.md, Pipeline file)"
_FLAT = (
    "the pipeline's lines sit directly on the page, with no region above "
    "them, and htrflow's ALTO and PAGE export writes only the text of lines "
    "inside a region: add a region Segmentation step before the line step " + _FIX
)
_NESTED = (
    "htrflow's ALTO and PAGE export writes only the text of lines inside a "
    "region (page, region, line) and of those lines' words, and this "
    "pipeline's segmentation puts its text elsewhere " + _FIX
)


class TextNotExported(ValueError):
    """The page's export holds none of its recognized text: a pipeline
    shape, not a condition, so the page is failed rather than retried."""


def _text(node: object) -> str:
    """A node's best transcription -- the one the templates write, first
    after htrflow sorts them by confidence -- whitespace-normalised the way
    an XML attribute value is."""
    transcription = getattr(node, "transcription", None) or []
    text = getattr(transcription[0], "text", None) if transcription else None
    return " ".join(text.split()) if isinstance(text, str) else ""


def recognized(node: object, depth: int = 0) -> list[tuple[int, str]]:
    """``(depth, text)`` of the deepest text on each branch of an htrflow
    page tree: a line's words where recognition gave it words, else the
    line. That is what the templates write when they write anything."""
    below = [
        found
        for child in getattr(node, "regions", None) or []
        for found in recognized(child, depth + 1)
    ]
    if below:
        return below
    text = _text(node)
    return [(depth, text)] if text else []


def exported(path: Path) -> Counter | None:
    """The texts one exported file holds: ALTO ``String/@CONTENT``, PAGE
    ``Unicode``. ``None`` for a file that does not parse -- the upload
    refuses that one with its own sentence (store.upload_page)."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    held: Counter = Counter()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        text = element.get("CONTENT") if tag == "String" else None
        if tag == "Unicode":
            text = element.text
        if text and (normalised := " ".join(text.split())):
            held[normalised] += 1
    return held


def _lines(n: int) -> str:
    return f"{n} line{'s' if n != 1 else ''}"


def check_export(tree: object, files: dict[str, Path], stem: str = "") -> None:
    """Raise ``TextNotExported`` when a format holds none of the page's
    recognized text; log when one holds only part of it."""
    found = recognized(tree)
    if not found:
        return  # a blank page: an empty export is the right one
    wanted = Counter(text for _, text in found)
    held: dict[str, int] = {}
    for fmt, path in files.items():
        texts = exported(path)
        if texts is not None:
            held[fmt] = len(found) - sum((wanted - texts).values())
    empty = [_NAMES.get(fmt, fmt) for fmt, n in held.items() if n == 0]
    if empty:
        cause = _FLAT if all(depth <= 1 for depth, _ in found) else _NESTED
        which = (
            f"the {' and '.join(empty[:-1])} and {empty[-1]} hold none of them"
            if len(empty) > 1
            else f"the {empty[0]} holds none of it"
        )
        raise TextNotExported(
            f"{NOT_EXPORTED}: htrflow recognized {_lines(len(found))} of text "
            f"on this page, but {which} — {cause}"
        )
    short = {fmt: n for fmt, n in held.items() if n < len(found)}
    if short:
        log.warning(
            "page %s: htrflow recognized %s of text, but %s — the rest sit "
            "where htrflow's export writes no text (a region the line model "
            "found no line in is read whole, outside any line)",
            stem,
            _lines(len(found)),
            " and ".join(f"the {_NAMES.get(f, f)} holds {n}" for f, n in short.items()),
        )
