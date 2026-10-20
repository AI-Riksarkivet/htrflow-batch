"""Viewer-facing outputs: ALTO dims + IIIF P3 manifest (docs: wrapper)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping

from .config import Config
from .iiif import PageRef, painting_body


def parse_alto_dims(root: ET.Element) -> tuple[int, int]:
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


def _language_map(value: object) -> dict:
    """Normalize any legal P2/P3 label to a P3 language map.

    P2 allows a plain string, an object (`{"@value", "@language"}`) or an
    array of either; only the P3 form (`{lang: [str, ...]}`) may pass through
    untouched — emitting a P2 object as if it were a language map corrupts
    the published manifest."""
    if isinstance(value, str):
        return {"none": [value]} if value else {}
    if isinstance(value, dict):
        entries: dict[str, object] = {str(k): v for k, v in value.items()}
        if "@value" in entries:
            text = entries["@value"]
            if not isinstance(text, str) or not text:
                return {}
            lang = entries.get("@language")
            return {lang if isinstance(lang, str) and lang else "none": [text]}
        return {k: v for k, v in entries.items() if v}
    if isinstance(value, list):
        merged: dict[str, list[str]] = {}
        for item in value:
            for lang, texts in _language_map(item).items():
                merged.setdefault(lang, []).extend(texts)
        return merged
    return {}


def _label(value: object, fallback: str) -> dict:
    """P2 labels are strings/objects/arrays; P3 consumers (UV) need a
    language map."""
    return _language_map(value) or {"none": [fallback]}


#: A plain web URI: what `rights` may be, as a viewer may link to it.
_WEB_URI = re.compile(r"https?://[^\s\"'<>`]+")
_DIRECTIONS = {"left-to-right", "right-to-left", "top-to-bottom", "bottom-to-top"}


def _carried(source: dict) -> dict:
    """What a viewer must or should show of the source, in P3 form: the
    credit line clients MUST render (requiredStatement), the licence, the
    description, and whether the canvases are a book read in some direction.
    P2 names these attribution, license, description and viewingHint. Every
    value is untrusted, so each is normalised to its P3 shape or left out."""
    p3 = isinstance(source.get("items"), list)
    statement = (
        source.get("requiredStatement")
        if p3
        else {"label": {"en": ["Attribution"]}, "value": source.get("attribution")}
    )
    licences = source.get("rights" if p3 else "license")
    licences = licences if isinstance(licences, list) else [licences]
    out = {
        "summary": _language_map(source.get("summary" if p3 else "description")),
        "metadata": [e for m in _items(source.get("metadata")) if (e := _pair(m))],
        "navDate": source.get("navDate")
        if isinstance(source.get("navDate"), str)
        else None,
        "behavior": _reading(source.get("behavior" if p3 else "viewingHint")),
        "rights": next(
            (u for u in (_uri(v) for v in licences) if _WEB_URI.fullmatch(u)), None
        ),
    }
    if isinstance(statement, dict):
        out["requiredStatement"] = _pair(statement, {"en": ["Attribution"]})
    if source.get("viewingDirection") in _DIRECTIONS:
        out["viewingDirection"] = source["viewingDirection"]
    return {k: v for k, v in out.items() if v}


def _items(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _pair(entry: dict, label: dict | None = None) -> dict | None:
    """A metadata or requiredStatement entry as P3 language maps, or None
    when it has no value (or no label, and none to fall back on): P3 needs
    both."""
    pair = {
        "label": _language_map(entry.get("label")) or label,
        "value": _language_map(entry.get("value")),
    }
    return pair if pair["label"] and pair["value"] else None


def _uri(value: object) -> str:
    """A licence as a string: itself, or a JSON-LD object's ``@id``."""
    value = value.get("@id") or value.get("id") if isinstance(value, dict) else value
    return value if isinstance(value, str) else ""


def _reading(hint: object) -> list[str]:
    """P3 ``behavior`` from a P3 list or a P2 ``viewingHint`` string."""
    return [
        v for v in (hint if isinstance(hint, list) else [hint]) if isinstance(v, str)
    ]


_QUALITY_LABEL = {"en": ["Predicted quality"]}


def _quality_entry(text: str) -> list[dict]:
    """One IIIF metadata pair; UV lists these in its "More information"
    panel as they are, with no patch."""
    return [{"label": _QUALITY_LABEL, "value": {"none": [text]}}]


def build_viewer_manifest(
    cfg: Config,
    source_manifest: dict,
    pages: "list[PageRef]",
    dims: "dict[str, tuple[int, int]]",
    quality: "Mapping[str, float] | None" = None,
    summary: dict | None = None,
) -> dict:
    base = cfg.results_url.rstrip("/")
    vol = f"{base}/{cfg.volume_prefix}"
    canvases = []
    for page in pages:
        if page.name not in dims:
            continue
        w, h = dims[page.name]
        src = page.canvas
        body = painting_body(src)
        canvas_id = f"{vol}/canvas/{page.name}"
        canvas = {
            "id": canvas_id,
            "type": "Canvas",
            "label": _label(src.get("label"), page.name),
            "width": w,
            "height": h,  # capped processing dims (D19 alignment)
            "seeAlso": [
                {
                    "id": f"{vol}/alto/{page.name}.xml",
                    "type": "Dataset",
                    "profile": "http://www.loc.gov/standards/alto",
                    "format": "application/xml",
                    "label": {"none": ["ALTO"]},
                }
            ],
            # what UV's download dialog lists for the current page; it never
            # reads seeAlso. Plain application/xml, since UV shows the
            # format's subtype next to the label.
            "rendering": [
                {
                    "id": f"{vol}/{fmt}/{page.name}.xml",
                    "type": "Text",
                    "format": "application/xml",
                    "label": {"none": [label]},
                }
                for fmt, label in (("alto", "ALTO"), ("page", "PAGE"))
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
        if behavior := _reading(src.get("behavior") or src.get("viewingHint")):
            canvas["behavior"] = behavior  # "non-paged": out of the page turning
        if quality and page.name in quality:
            canvas["metadata"] = _quality_entry(f"{quality[page.name]:.2f}")
        canvases.append(canvas)
    manifest = {
        "@context": "http://iiif.io/api/presentation/3/context.json",
        "id": f"{vol}/iiif.json",
        "type": "Manifest",
        "label": _label(source_manifest.get("label"), cfg.volume_ref),
        **_carried(source_manifest),
        "items": canvases,
    }
    if summary is not None:
        manifest["metadata"] = manifest.get("metadata", []) + _quality_entry(
            f"mean {summary['mean']:.2f}, lowest {summary['min']:.2f}, "
            f"over {summary['scored']} pages"
        )
    return manifest
