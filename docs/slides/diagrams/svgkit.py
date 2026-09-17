"""Tiny builder for the hand-drawn slide diagrams: boxes with an icon on the
left, groups, arrows. Icons are embedded as data URIs so each diagram is one
self-contained SVG file."""
import base64, os, re

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
MAGENTA, INK, MUTED, PANEL, LINE = "#b44b6f", "#212121", "#6d6265", "#f5f2f3", "#d9cdd1"


def icon_uri(name, color=None):
    s = open(os.path.join(ICONS, name + ".svg"), encoding="utf-8").read()
    if color:
        s = s.replace('stroke="currentColor"', f'stroke="{color}"')
    return "data:image/svg+xml;base64," + base64.b64encode(s.encode()).decode()


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Diagram:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def group(self, x, y, w, h, label):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" stroke="{LINE}" stroke-width="2"/>'
            f'<text x="{x+18}" y="{y+28}" font-size="16" font-weight="600" fill="{MUTED}" letter-spacing="0.6">{esc(label.upper())}</text>')

    def box(self, x, y, w, h, title, sub=None, icon=None, color=None, external=False, isize=34):
        fill = "#ffffff" if external else PANEL
        stroke = MUTED if external else MAGENTA
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        tx = x + w / 2
        if icon:
            ix, iy = x + 12, y + (h - isize) / 2
            self.parts.append(f'<image href="{icon_uri(icon, color)}" x="{ix}" y="{iy}" width="{isize}" height="{isize}"/>')
            tx = x + 12 + isize + (w - 12 - isize) / 2
        if sub:
            self.parts.append(f'<text x="{tx}" y="{y+h/2-4}" text-anchor="middle" font-size="19" fill="{INK}">{esc(title)}</text>')
            self.parts.append(f'<text x="{tx}" y="{y+h/2+18}" text-anchor="middle" font-size="14" fill="{MUTED}">{esc(sub)}</text>')
        else:
            self.parts.append(f'<text x="{tx}" y="{y+h/2+7}" text-anchor="middle" font-size="19" fill="{INK}">{esc(title)}</text>')

    def vbox(self, x, y, w, h, title, sub=None, icon=None, color=None, external=False, isize=38, strong=False, bad=False):
        """A box with the icon on top and the text under it; `sub` may hold
        one line break ("\n")."""
        fill = "#ffffff" if external else PANEL
        stroke = MUTED if external else MAGENTA
        sw = 4 if strong else 2
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        cx = x + w / 2
        top = y + 12
        if icon:
            self.parts.append(f'<image href="{icon_uri(icon, color)}" x="{cx-isize/2}" y="{top}" width="{isize}" height="{isize}"/>')
            top += isize + 6
        weight = ' font-weight="600"' if strong else ""
        tcolor = "#a3243b" if bad else INK
        self.parts.append(f'<text x="{cx}" y="{top+18}" text-anchor="middle" font-size="19" fill="{tcolor}"{weight}>{esc(title)}</text>')
        if sub:
            for k, line in enumerate(sub.split("\n")):
                self.parts.append(f'<text x="{cx}" y="{top+40+k*18}" text-anchor="middle" font-size="14" fill="{MUTED}">{esc(line)}</text>')

    def row(self, y, h, items, x0=20, gap=34, w=None, total=1240, **kw):
        """Lay out vboxes left to right with arrows between them; returns the
        list of (x, w) so callers can draw extra arrows."""
        n = len(items)
        if w is None:
            w = (total - gap * (n - 1)) / n
        placed = []
        for i, it in enumerate(items):
            x = x0 + i * (w + gap)
            title, sub, icon = it[:3]
            opts = it[3] if len(it) > 3 else {}
            self.vbox(x, y, w, h, title, sub, icon, **{**kw, **opts})
            placed.append((x, w))
            if i:
                px, pw = placed[i - 1]
                self.arrow([(px + pw, y + h / 2), (x - 2, y + h / 2)])
        return placed

    def cylinder(self, x, y, w, h, title, icon=None):
        r = 14
        self.parts.append(
            f'<path d="M{x},{y+r} a{w/2},{r} 0 0,1 {w},0 v{h-2*r} a{w/2},{r} 0 0,1 {-w},0 z" fill="{PANEL}" stroke="{MAGENTA}" stroke-width="2"/>'
            f'<path d="M{x},{y+r} a{w/2},{r} 0 0,0 {w},0" fill="none" stroke="{MAGENTA}" stroke-width="2"/>')
        tx = x + w / 2
        if icon:
            self.parts.append(f'<image href="{icon_uri(icon, MAGENTA)}" x="{x+14}" y="{y+h/2-12}" width="30" height="30"/>')
            tx = x + 44 + (w - 44) / 2
        self.parts.append(f'<text x="{tx}" y="{y+h/2+12}" text-anchor="middle" font-size="19" fill="{INK}">{esc(title)}</text>')

    def arrow(self, pts, dashed=False, label=None, lx=None, ly=None, anchor="middle"):
        d = "M" + " L".join(f"{a},{b}" for a, b in pts)
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{MAGENTA}" stroke-width="2.2" marker-end="url(#arrow)"{dash}/>')
        if label:
            self.parts.append(f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" font-size="14" fill="{MUTED}">{esc(label)}</text>')

    def text(self, x, y, t, size=16, color=MUTED, anchor="middle"):
        self.parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" fill="{color}">{esc(t)}</text>')

    def save(self, path):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" '
                f'font-family="Open Sans, sans-serif"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MAGENTA}"/></marker></defs>')
        open(path, "w", encoding="utf-8").write(head + "".join(self.parts) + "</svg>")
