"""Viewer-facing outputs: ALTO dims + IIIF P3 manifest (docs: wrapper)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .config import Config
from .iiif import PageRef


def parse_alto_dims_bytes(data: bytes) -> tuple[int, int]:
    root = ET.fromstring(data)
    candidates = []
    for elem in root.iter():
        w, h = elem.get("WIDTH"), elem.get("HEIGHT")
        if w is not None and h is not None:
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag == "Page":
                return int(float(w)), int(float(h))
            candidates.append((int(float(w)), int(float(h))))
    if candidates:
        return candidates[0]
    raise ValueError("no WIDTH/HEIGHT element in ALTO data")


def parse_alto_dims(path: Path) -> tuple[int, int]:
    try:
        return parse_alto_dims_bytes(Path(path).read_bytes())
    except ValueError:
        raise ValueError(f"no WIDTH/HEIGHT element in {path}") from None


def _thumbnail(body: dict, w: int, h: int) -> "list[dict] | None":
    """UV renders no thumbnail at all when the property is absent and there
    is no image service; prefer a sized IIIF request (width syntax — lbiiif
    501s on !w,h), else fall back to the full static image."""
    for svc in body.get("service") or []:
        svc_id = svc.get("id") or svc.get("@id")
        if svc_id:
            return [
                {
                    "id": f"{svc_id}/full/200,/0/default.jpg",
                    "type": "Image",
                    "format": "image/jpeg",
                }
            ]
    if body.get("id"):
        return [
            {
                "id": body["id"],
                "type": "Image",
                "format": body.get("format", "image/jpeg"),
                "width": w,
                "height": h,
            }
        ]
    return None


def build_viewer_manifest(
    cfg: Config,
    source_manifest: dict,
    pages: "list[PageRef]",
    dims: "dict[str, tuple[int, int]]",
) -> dict:
    base = cfg.public_results_base.rstrip("/")
    vol = f"{base}/{cfg.volume_prefix}"
    canvases = []
    for page in pages:
        if page.name not in dims:
            continue
        w, h = dims[page.name]
        src = page.canvas
        body = {}
        for ap in src.get("items", []):
            for anno in ap.get("items", []):
                if anno.get("body"):
                    body = anno["body"]
        canvas_id = f"{vol}/canvas/{page.name}"
        thumbnail = _thumbnail(body, w, h)
        canvases.append(
            {
                "id": canvas_id,
                "type": "Canvas",
                "label": src.get("label", {"none": [page.name]}),
                "width": w,
                "height": h,  # capped processing dims (D19 alignment)
                **({"thumbnail": thumbnail} if thumbnail else {}),
                "seeAlso": [
                    {
                        "id": f"{vol}/alto/{page.name}.xml",
                        "type": "Dataset",
                        "profile": "http://www.loc.gov/standards/alto/ns-v4#",
                        "format": "application/xml+alto",
                        "label": {"none": ["ALTO"]},
                    }
                ],
                "items": [
                    {
                        "id": f"{canvas_id}/ap",
                        "type": "AnnotationPage",
                        "items": [
                            {
                                "id": f"{canvas_id}/anno",
                                "type": "Annotation",
                                "motivation": "painting",
                                "target": canvas_id,
                                "body": body,
                            }
                        ],
                    }
                ],
            }
        )
    return {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": f"{vol}/iiif.json",
        "type": "Manifest",
        "label": source_manifest.get("label", {"none": [cfg.volume_ref]}),
        # UV4 (RA fork) shows the ALTO text panel only when
        # getSearchService() finds a search service; endpoint is a stub.
        "service": [
            {
                "@context": "http://iiif.io/api/search/1/context.json",
                "@id": f"{vol}/search",
                "@type": "SearchService1",
                "profile": "http://iiif.io/api/search/1/search",
            }
        ],
        "items": canvases,
    }
