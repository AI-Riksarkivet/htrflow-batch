"""Tiny builder for the hand-drawn slide diagrams.

One style for every diagram: white cards on pale panels, an icon on a tinted
tile, right-angle arrows with rounded bends, labels in small pills. Icons and
the two Open Sans weights are embedded, so each diagram is one self-contained
SVG file that draws the same everywhere.

Sizes assume the diagram is shown at about 0.7 of its canvas width (a 1600
wide canvas at ``w:1120``): card titles land near 19px on the slide and the
line under them near 14px, the smallest a room can read.

Every label is measured against Open Sans's own glyph widths
(``opensans-widths.json``, read from the bundled fonts with fontTools), and
``save`` refuses a diagram whose text would not fit its card.
"""
import base64, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "icons")
FONTS = os.path.join(HERE, "..", "theme", "fonts")
WIDTHS = json.load(open(os.path.join(HERE, "opensans-widths.json"), encoding="utf-8"))

MAGENTA, INK, MUTED, RED = "#b44b6f", "#1f1a1c", "#6d6265", "#a3243b"
TINT, NEUTRAL, BADTINT = "#f8e9ef", "#f1ecee", "#fbe7eb"
HAIR, PANEL, LABEL, DOTTED = "#e4d9dd", "#f7f3f4", "#574d50", "#b3a7ab"

TITLE, SUB, GROUP, PILL = 27, 20, 21, 18
CARD_H = 96                   # a card: tile on the left, title and one line
TILE, ICON, LOGO = 60, 36, 42
GAP = 6                       # air between an arrowhead and its card


def text_width(t, size, weight=400):
    table = WIDTHS[str(weight)]
    return sum(table.get(c, 0.6) for c in t) * size


def icon_uri(name, color):
    s = open(os.path.join(ICONS, name + ".svg"), encoding="utf-8").read()
    if name.startswith("lucide-"):
        s = s.replace('stroke="currentColor"', f'stroke="{color}"')
        s = s.replace('stroke-width="2"', 'stroke-width="1.6"')
    return "data:image/svg+xml;base64," + base64.b64encode(s.encode()).decode()


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tall_height(sub):
    """Height of a tall card holding `sub` (0, 1 or 2 lines)."""
    return 146 + 30 * (len(sub.split("\n")) if sub else 0)


class Diagram:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.back, self.front, self.errors = [], [], []

    # ------------------------------------------------------------ checks
    def _fits(self, t, size, weight, room):
        need = text_width(t, size, weight)
        if need > room:
            self.errors.append(f"{t!r} needs {need:.0f}px, has {room:.0f}px")

    # ------------------------------------------------------------ panels
    def group(self, x, y, w, h, label, icon=None, outside=False):
        """A pale panel with its label top left; `outside` draws a dotted
        outline instead, for things that are not part of the platform."""
        if outside:
            self.back.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="none" stroke="{DOTTED}" '
                             'stroke-width="2" stroke-dasharray="2 8" stroke-linecap="round"/>')
        else:
            self.back.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{PANEL}"/>')
        tx = x + 24
        if icon:
            self.back.append(f'<image href="{icon_uri(icon, MUTED)}" x="{x+22}" y="{y+15}" width="26" height="26"/>')
            tx += 36
        self._fits(label, GROUP, 600, x + w - 16 - tx)
        self.back.append(f'<text x="{tx}" y="{y+36}" font-size="{GROUP}" font-weight="600" fill="{LABEL}" '
                         f'letter-spacing="0.3">{esc(label)}</text>')

    # ------------------------------------------------------------ cards
    def _frame(self, x, y, w, h, strong, bad):
        stroke, sw, flt = (MAGENTA, 3, "glow") if strong else (RED, 3, "sh") if bad else (HAIR, 1.5, "sh")
        self.front.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#ffffff" '
                          f'stroke="{stroke}" stroke-width="{sw}" filter="url(#{flt})"/>')

    def _tile(self, tx, ty, icon, logo, strong, bad, outside):
        if strong and logo:
            fill, color = TINT, MAGENTA
        elif strong:
            fill, color = MAGENTA, "#ffffff"
        elif bad:
            fill, color = BADTINT, RED
        elif logo or outside:
            fill, color = NEUTRAL, MUTED
        else:
            fill, color = TINT, MAGENTA
        self.front.append(f'<rect x="{tx}" y="{ty}" width="{TILE}" height="{TILE}" rx="14" fill="{fill}"/>')
        if icon:
            s = LOGO if logo else ICON
            self.front.append(f'<image href="{icon_uri(icon, color)}" x="{tx+(TILE-s)/2}" y="{ty+(TILE-s)/2}" '
                              f'width="{s}" height="{s}"/>')

    def card(self, x, y, w, title, sub=None, icon=None, logo=False, strong=False, bad=False, outside=False):
        """A wide card: tile on the left, title and one short line beside it."""
        h = CARD_H
        self._frame(x, y, w, h, strong, bad)
        self._tile(x + 18, y + (h - TILE) / 2, icon, logo, strong, bad, outside)
        tx = x + 18 + TILE + 18
        room = x + w - 18 - tx
        self._fits(title, TITLE, 600, room)
        color = RED if bad else INK
        if sub:
            self._fits(sub, SUB, 400, room)
            self.front.append(f'<text x="{tx}" y="{y+h/2-4}" font-size="{TITLE}" font-weight="600" fill="{color}">{esc(title)}</text>'
                              f'<text x="{tx}" y="{y+h/2+25}" font-size="{SUB}" fill="{MUTED}">{esc(sub)}</text>')
        else:
            self.front.append(f'<text x="{tx}" y="{y+h/2+10}" font-size="{TITLE}" font-weight="600" fill="{color}">{esc(title)}</text>')
        return (x, y, w, h)

    def tall(self, x, y, w, title, sub=None, icon=None, logo=False, strong=False, bad=False, outside=False, n=None):
        """A narrow card: tile on top, title, then up to two short lines."""
        h = tall_height(sub)
        self._frame(x, y, w, h, strong, bad)
        cx = x + w / 2
        self._tile(cx - TILE / 2, y + 22, icon, logo, strong, bad, outside)
        room = w - 32
        self._fits(title, TITLE, 600, room)
        color = RED if bad else INK
        self.front.append(f'<text x="{cx}" y="{y+122}" text-anchor="middle" font-size="{TITLE}" font-weight="600" fill="{color}">{esc(title)}</text>')
        for k, line in enumerate(sub.split("\n") if sub else []):
            self._fits(line, SUB, 400, room)
            self.front.append(f'<text x="{cx}" y="{y+152+k*28}" text-anchor="middle" font-size="{SUB}" fill="{MUTED}">{esc(line)}</text>')
        if n is not None:
            self.front.append(f'<circle cx="{x+8}" cy="{y+8}" r="18" fill="{MAGENTA}"/>'
                              f'<text x="{x+8}" y="{y+15}" text-anchor="middle" font-size="20" font-weight="600" fill="#ffffff">{n}</text>')
        return (x, y, w, h)

    def row(self, y, items, x0=24, total=1552, gap=48):
        """Tall cards left to right, joined by arrows. Items are
        (title, sub, icon) or (title, sub, icon, {options}). Returns the
        cards' boxes."""
        n = len(items)
        w = (total - gap * (n - 1)) / n
        h = max(tall_height(it[1]) for it in items)
        boxes = []
        for i, it in enumerate(items):
            x = x0 + i * (w + gap)
            opts = it[3] if len(it) > 3 else {}
            box = self.tall(x, y + (h - tall_height(it[1])) / 2, w, it[0], it[1], it[2], **opts)
            boxes.append(box)
            if i:
                self.arrow([(x - gap, y + h / 2), (x - GAP, y + h / 2)])
        return boxes

    # ------------------------------------------------------------ lines
    def arrow(self, pts, label=None, at=None, dashed=False, dot=True, head=True):
        """A right-angle path through `pts` with rounded bends. `label` sits in
        a pill centred on `at` (default: the middle of the longest segment)."""
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if ax != bx and ay != by:
                self.errors.append(f"diagonal arrow segment {(ax, ay)} -> {(bx, by)}")
        r = 14
        d = f"M{pts[0][0]},{pts[0][1]}"
        for i in range(1, len(pts) - 1):
            (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[i + 1]
            ux, uy = (bx > ax) - (bx < ax), (by > ay) - (by < ay)
            vx, vy = (cx > bx) - (cx < bx), (cy > by) - (cy < by)
            d += f" L{bx-ux*r},{by-uy*r} Q{bx},{by} {bx+vx*r},{by+vy*r}"
        d += f" L{pts[-1][0]},{pts[-1][1]}"
        if dot:
            self.back.append(f'<circle cx="{pts[0][0]}" cy="{pts[0][1]}" r="4.5" fill="{MAGENTA}"/>')
        dash = ' stroke-dasharray="9 8"' if dashed else ""
        marker = ' marker-end="url(#ar)"' if head else ""
        self.back.append(f'<path d="{d}" fill="none" stroke="{MAGENTA}" stroke-width="2.75" stroke-linecap="round" '
                         f'stroke-linejoin="round"{marker}{dash}/>')
        if label:
            if at is None:
                a, b = max(zip(pts, pts[1:]), key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
                at = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            self.pill(*at, label)

    def pill(self, x, y, label):
        w = text_width(label, PILL, 600) + 26
        self.front.append(f'<rect x="{x-w/2}" y="{y-17}" width="{w}" height="34" rx="17" fill="#ffffff" stroke="{HAIR}" stroke-width="1.5"/>'
                          f'<text x="{x}" y="{y+6}" text-anchor="middle" font-size="{PILL}" font-weight="600" fill="{MUTED}">{esc(label)}</text>')

    # ------------------------------------------------------------ output
    def save(self, path):
        if self.errors:
            raise SystemExit(f"{os.path.basename(path)}:\n  " + "\n  ".join(self.errors))
        fonts = ""
        for name, weight in (("OpenSans-Regular", 400), ("OpenSans-SemiBold", 600)):
            b64 = base64.b64encode(open(os.path.join(FONTS, name + ".woff2"), "rb").read()).decode()
            fonts += ('@font-face{font-family:"Open Sans";font-style:normal;font-weight:%d;font-display:block;'
                      'src:url("data:font/woff2;base64,%s") format("woff2");}' % (weight, b64))
        defs = ('<defs>'
                '<filter id="sh" x="-10%" y="-20%" width="120%" height="160%" color-interpolation-filters="sRGB">'
                '<feDropShadow dx="0" dy="3" stdDeviation="6" flood-color="#3a1f2a" flood-opacity="0.10"/></filter>'
                '<filter id="glow" x="-20%" y="-40%" width="140%" height="200%" color-interpolation-filters="sRGB">'
                f'<feDropShadow dx="0" dy="4" stdDeviation="10" flood-color="{MAGENTA}" flood-opacity="0.30"/></filter>'
                '<marker id="ar" viewBox="0 0 14 14" refX="11" refY="7" markerWidth="14" markerHeight="14" '
                'markerUnits="userSpaceOnUse" orient="auto">'
                f'<path d="M3,2 L11,7 L3,12" fill="none" stroke="{MAGENTA}" stroke-width="2.75" stroke-linecap="round" '
                'stroke-linejoin="round"/></marker></defs>')
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" '
                f'height="{self.h}" font-family="Open Sans, sans-serif"><style>{fonts}</style>{defs}')
        with open(path, "w", encoding="utf-8") as f:
            f.write(head + "".join(self.back) + "".join(self.front) + "</svg>")
