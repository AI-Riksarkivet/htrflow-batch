"""Build a synthetic P3 manifest for ``IMAGES`` volumes (docs: wrapper).

Proven pattern: LoC Lincoln papers run 2026-07-29. Bare image URLs carry no
IIIF image service, so annotation bodies hold nothing but the direct image
URL -- the wrapper fetches ``body.id`` as-is and canvas dimensions are
recovered from the ALTO output later.
"""

from __future__ import annotations

from collections.abc import Sequence


def build_manifest(volume_id: str, image_urls: Sequence[str], manifest_id: str) -> dict:
    """Build a minimal valid P3 manifest with one canvas per image URL."""
    canvases = []
    for i, url in enumerate(image_urls, start=1):
        cid = f"{manifest_id.rsplit('/', 1)[0]}/canvas/{i}"
        canvases.append(
            {
                "id": cid,
                "type": "Canvas",
                "label": {"none": [f"Image {i}"]},
                "items": [
                    {
                        "id": f"{cid}/ap",
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": f"{cid}/anno",
                                "type": "Annotation",
                                "motivation": "painting",
                                "target": cid,
                                "body": {
                                    "id": url,
                                    "type": "Image",
                                    "format": "image/jpeg",
                                },
                            }
                        ],
                    }
                ],
            }
        )
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": manifest_id,
        "type": "Manifest",
        "label": {"none": [volume_id]},
        "items": canvases,
    }
