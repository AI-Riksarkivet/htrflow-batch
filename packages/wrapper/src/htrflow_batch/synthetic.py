"""The canvases of an ``IMAGES`` volume (docs: wrapper).

Proven pattern: LoC Lincoln papers run 2026-07-29. Bare image URLs come with
no manifest and no IIIF image service, so each becomes a canvas whose one
painting body is the URL itself: the wrapper fetches ``body.id`` as-is and
the viewer manifest publishes it. They are not published as a manifest: a
Presentation 3 canvas must have a size, and an image's is known only once it
is fetched -- ``iiif.json``, built afterwards, carries them.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Sequence
from urllib.parse import urlsplit


def image_canvases(image_urls: Sequence[str]) -> dict:
    """The page list, in the shape of a P3 manifest's ``items``."""
    canvases = []
    for i, url in enumerate(image_urls, start=1):
        body = {"id": url, "type": "Image"}
        if kind := mimetypes.guess_type(urlsplit(url).path)[0]:
            body["format"] = kind
        canvases.append(
            {
                "label": {"none": [f"Image {i}"]},
                "items": [{"items": [{"motivation": "painting", "body": body}]}],
            }
        )
    return {"items": canvases}
