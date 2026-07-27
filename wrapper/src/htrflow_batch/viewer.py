"""Viewer-facing outputs: ALTO dims + IIIF P3 manifest (DESIGN.md D19)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def parse_alto_dims(path: Path) -> tuple[int, int]:
    for _, elem in ET.iterparse(str(path)):
        w, h = elem.get("WIDTH"), elem.get("HEIGHT")
        if w is not None and h is not None:
            return int(float(w)), int(float(h))
    raise ValueError(f"no WIDTH/HEIGHT element in {path}")
