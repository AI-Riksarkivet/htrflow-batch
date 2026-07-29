"""Synthetic P3 manifests for ``images:`` volumes, and source pre-validation.

Proven pattern: LoC Lincoln papers run 2026-07-29 (spec §7.4). Bare image URLs
carry no IIIF image service, so annotation bodies hold nothing but the direct
image URL -- the wrapper fetches ``body.id`` as-is and canvas dimensions are
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


def classify_manifest(doc: dict) -> str:
    """Classify a fetched manifest as ``"p3"``, ``"p2"`` or ``"unsupported"``.

    Used by the tick to pre-validate ``manifest:`` volumes (spec §4.4) so an
    unusable source fails fast instead of being submitted as a job.
    """
    if doc.get("items"):
        return "p3"
    seqs = doc.get("sequences") or []
    if seqs and (seqs[0].get("canvases") or []):
        return "p2"
    return "unsupported"
