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

__all__ = [
    "PageRuleError",
    "info_url",
    "manifest_sizes",
    "page_number",
    "parse_page_set",
    "quality_mean",
]

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


def service_url(source_url: str) -> str:
    """The image service an image request was made of: the identifier's URL."""
    origin, segments = _split_request(source_url)
    return f"{origin}/{'/'.join(segments[:-_REQUEST_SEGMENTS])}"


def info_url(source_url: str) -> str:
    """The image service's ``info.json``: the identifier's URL plus ``/info.json``."""
    return f"{service_url(source_url)}/info.json"


def _size(value: object) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok else None  # type: ignore[return-value]


def _service_ids(service: object) -> set[str]:
    """The service ids of an image, unescaped and without a trailing slash."""
    services = service if isinstance(service, list) else [service]
    return {
        _service_key(sid)
        for s in services
        if isinstance(s, dict) and isinstance(sid := s.get("id") or s.get("@id"), str)
    }


def _service_key(url: str) -> str:
    return unquote(url).rstrip("/")


def _list(value: object, what: str, n: int) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise PageRuleError(f"canvas {n}: {what} is not a list of objects")
    return value


def _canvas_images(doc: dict) -> list[tuple[dict, list[dict]]]:
    """``(canvas, images)`` for every canvas of a Presentation 3 or 2
    manifest. A canvas of the wrong shape is refused, never passed over:
    the volume fails with a reason instead of the run with a traceback."""
    canvases = doc.get("items")
    if canvases is None:  # Presentation 2: sequences[0].canvases
        seqs = doc.get("sequences")
        first = seqs[0] if isinstance(seqs, list) and seqs else {}
        canvases = first.get("canvases") if isinstance(first, dict) else None
    if not isinstance(canvases, list):
        raise PageRuleError("the file is not an IIIF manifest (no list of canvases)")
    found = []
    for n, canvas in enumerate(canvases, start=1):
        if not isinstance(canvas, dict):
            raise PageRuleError(f"canvas {n} is not an object")
        images = []
        for page in _list(canvas.get("items"), "items", n):  # P3
            for anno in _list(page.get("items"), "an annotation page's items", n):
                body = anno.get("body")
                if body is not None and not isinstance(body, dict):
                    raise PageRuleError(f"canvas {n}: an image is not an object")
                if body is not None:
                    images.append(body)
        for anno in _list(canvas.get("images"), "images", n):  # P2
            res = anno.get("resource")
            if res is not None and not isinstance(res, dict):
                raise PageRuleError(f"canvas {n}: an image is not an object")
            if res is not None:
                images.append(res)
        found.append((canvas, images))
    return found


def manifest_sizes(
    doc: object, recorded_url: str, sources: dict[str, str]
) -> dict[str, tuple[int, int]]:
    """Each page's full scan size, read from the volume's own IIIF manifest
    (a downloaded copy) instead of one ``info.json`` per page.

    The size is the canvas's: on the archive's manifests it is the scan's
    native size. A page is found by its image service, the identifier its
    ``page_sources`` request was made of. Refused rather than guessed: the
    manifest of another volume, a page not in it or on two canvases, a canvas
    without a size, and an image whose own size disagrees with its canvas.
    """
    if not isinstance(doc, dict):
        raise PageRuleError("the file is not an IIIF manifest (not a JSON object)")
    ident = doc.get("id") or doc.get("@id")
    if ident != recorded_url:
        raise PageRuleError(
            f"the file is the manifest {ident!r}, not the volume's {recorded_url!r}"
        )
    # service -> [(canvas, image)], one entry per canvas: a canvas naming
    # its image twice is still one canvas.
    by_service: dict[str, list[tuple[dict, dict]]] = {}
    for canvas, images in _canvas_images(doc):
        mine: dict[str, dict] = {}
        for image in images:
            for sid in _service_ids(image.get("service")):
                mine.setdefault(sid, image)
        for sid, image in mine.items():
            by_service.setdefault(sid, []).append((canvas, image))
    sizes: dict[str, tuple[int, int]] = {}
    for key in sorted(sources):
        hits = by_service.get(_service_key(service_url(sources[key])), [])
        if not hits:
            raise PageRuleError(f"page {key}: its image is not in the manifest")
        if len(hits) > 1:
            raise PageRuleError(f"page {key}: its image is on two canvases")
        canvas, image = hits[0]
        width, height = _size(canvas.get("width")), _size(canvas.get("height"))
        if width is None or height is None:
            raise PageRuleError(
                f"page {key}: canvas size {canvas.get('width')!r}x"
                f"{canvas.get('height')!r} is not two positive integers"
            )
        stated = (image.get("width"), image.get("height"))
        own = tuple(_size(v) for v in stated)
        if any(v is not None and _size(v) is None for v in stated):
            raise PageRuleError(
                f"page {key}: the image states a size {stated[0]!r}x"
                f"{stated[1]!r} that is not two positive integers"
            )
        if all(own) and own != (width, height):
            raise PageRuleError(
                f"page {key}: the image is {own[0]}x{own[1]}, "
                f"its canvas {width}x{height}"
            )
        sizes[key] = (width, height)
    return sizes


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
