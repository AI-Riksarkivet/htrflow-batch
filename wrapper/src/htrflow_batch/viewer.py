"""Viewer-facing outputs: ALTO dims + IIIF P3 manifest (DESIGN.md D19)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def parse_alto_dims(path: Path) -> tuple[int, int]:
    root = ET.parse(str(path)).getroot()
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
    raise ValueError(f"no WIDTH/HEIGHT element in {path}")
