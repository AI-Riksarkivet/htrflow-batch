#!/usr/bin/env python3
"""Embed one webfont in a rendered Mermaid SVG, as a data URI.

An SVG referenced from an ``<img>`` may not load anything from outside
itself, so a ``font-family`` the viewer does not have falls back silently.
Inlining the face makes the committed SVG draw the same everywhere: in the
slide deck, in a browser, in whatever the file is pasted into next.

Only the regular weight is embedded. Mermaid draws every label at normal
weight, and a second face would double the file for nothing.
"""

from __future__ import annotations

import base64
import pathlib
import re
import sys


def main(svg_path: str, woff2_path: str) -> int:
    svg = pathlib.Path(svg_path)
    text = svg.read_text(encoding="utf-8")
    if "@font-face" in text:
        return 0

    b64 = base64.b64encode(pathlib.Path(woff2_path).read_bytes()).decode()
    face = (
        "<style>"
        '@font-face{font-family:"Open Sans";font-style:normal;font-weight:400;'
        "font-display:block;"
        f'src:url("data:font/woff2;base64,{b64}") format("woff2");}}'
        "</style>"
    )

    def insert(match: "re.Match[str]") -> str:
        return match.group(1) + face

    text, count = re.subn(r"(<svg\b[^>]*>)", insert, text, count=1)
    if count != 1:
        print(f"no <svg> element in {svg}", file=sys.stderr)
        return 1
    svg.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: slides_embed_font.py <svg> <woff2>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
