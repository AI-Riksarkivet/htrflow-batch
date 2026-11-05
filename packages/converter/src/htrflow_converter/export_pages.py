"""The page rules of ``htrflow-campaigns export``: pure functions, no I/O.

Which pages a publication keeps, what a page file is called, where its
image service lives, and how good a volume is on average. They sit apart
from the command so each rule can be tested on its own.

Every rule that cannot be satisfied raises ``PageRuleError`` -- the command
turns it into one sentence, instead of exporting a page under a guessed name.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

__all__ = ["PageRuleError", "info_url", "page_number", "parse_page_set", "quality_mean"]

# An IIIF Image API request ends in {region}/{size}/{rotation}/{quality}.{format};
# the identifier is the path segment just before those four.
_REQUEST_SEGMENTS = 4


class PageRuleError(ValueError):
    """A page rule that the input does not satisfy."""


def parse_page_set(text: str) -> frozenset[int]:
    """``"1,3-4"`` -> ``{1, 3, 4}``; ``"none"`` -> the empty set.

    Page numbers count from 1. A stray comma, an empty string or a reversed
    range is an error, not a silent shrug: the set decides which pages are
    blanked, so a misread one would publish or hide the wrong pages.
    """
    if text == "none":
        return frozenset()
    pages: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if m is None:
            raise PageRuleError(f"not a page set: {text!r} (use e.g. 1,3-4 or none)")
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) is not None else first
        if first < 1 or last < first:
            raise PageRuleError(f"not a page range: {part!r} in {text!r}")
        pages.update(range(first, last + 1))
    return frozenset(pages)


def _split_request(source_url: str) -> tuple[str, list[str]]:
    """``(scheme://host, path segments)``; an image request needs the four
    request segments plus an identifier after the host."""
    parts = urlsplit(source_url)
    segments = [s for s in parts.path.split("/") if s]
    if not parts.scheme or not parts.netloc or len(segments) < _REQUEST_SEGMENTS + 1:
        raise PageRuleError(f"not an IIIF image request: {source_url!r}")
    return f"{parts.scheme}://{parts.netloc}", segments


def page_number(source_url: str, volume_id: str) -> int:
    """The page number in the source image id (``<volume>_00417`` -> 417).

    The file number comes from the archive's own image id, never from the
    position in the campaign: pages can be skipped or reordered, the id
    cannot. An id of another volume, or without exactly five digits, is
    refused rather than numbered.
    """
    _, segments = _split_request(source_url)
    identifier = unquote(segments[-_REQUEST_SEGMENTS - 1])
    m = re.search(rf"(?:^|[!/:]){re.escape(volume_id)}_(\d{{5}})$", identifier)
    if m is None:
        raise PageRuleError(f"{source_url!r} is not an image of volume {volume_id}")
    return int(m.group(1))


def info_url(source_url: str) -> str:
    """The image service's ``info.json``: the identifier's URL plus ``/info.json``."""
    origin, segments = _split_request(source_url)
    return f"{origin}/{'/'.join(segments[:-_REQUEST_SEGMENTS])}/info.json"


def quality_mean(
    results: dict[str, dict], empty_keys: set[str]
) -> tuple[float | None, int]:
    """``(mean, pages counted)`` over the pages whose score says something.

    Left out: pages being emptied (``empty_keys``), pages without a score
    (failed, never scored), and a score of exactly 0 -- the quality model's
    "found nothing", which would drag the mean of a volume with blank leaves.
    ``(None, 0)`` when nothing counts.
    """
    scores = [
        q
        for key, r in results.items()
        if key not in empty_keys
        and isinstance(q := r.get("quality"), int | float)
        and q > 0
    ]
    if not scores:
        return None, 0
    return sum(scores) / len(scores), len(scores)
