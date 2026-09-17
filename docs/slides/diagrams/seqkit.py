"""Sequences drawn as numbered step lists, for a narrow page.

A lifeline diagram with seven participants cannot be read in a 690 px text
column, so a sequence is drawn as rows instead: a number, who acts, whom it
acts on, and what happens. A loop is a panel around its rows; a note is a
dotted row of its own. Everything else comes from svgkit.
"""
from svgkit import (Diagram, GAP, INK, MUTED, TINT, NEUTRAL, HAIR, MAGENTA, DOTTED,
                    icon_uri, esc, text_width)

CHIP_W, CHIP_H, TEXT, ROW_MIN, LINE = 208, 44, 20, 64, 28


def wrap(text, width, size=TEXT, weight=400):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and text_width(trial, size, weight) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


class Steps:
    """`actors` maps a name to (label, icon, outside). Rows are
    ("msg", src, dst, text), ("reply", src, dst, text), ("loop", label, rows)
    or ("note", text)."""

    def __init__(self, width, actors, rows, title=None, title_icon=None):
        self.w, self.actors, self.rows, self.title, self.title_icon = width, actors, rows, title, title_icon
        self.text_x = 24 + 24 + 36 + 16 + CHIP_W + 44 + CHIP_W + 24
        self.h = self._height(rows, 0) + (56 if title else 24) + 40

    def _text_width(self, indent):
        # A loop's panel ends 16 px short of the list's own edge, and keeps 24 px inside.
        return self.w - 48 - self.text_x - indent - (40 if indent else 8)

    def _height(self, rows, indent):
        h = 0
        for r in rows:
            if r[0] in ("msg", "reply"):
                h += max(ROW_MIN, len(wrap(r[3], self._text_width(indent))) * LINE + 28)
            elif r[0] == "note":
                h += len(wrap(r[1], self.w - 48 - 96 - indent - 32)) * LINE + 36 + 12
            elif r[0] == "loop":
                h += 56 + self._height(r[2], indent + 24) + 24 + 12
        return h

    def draw(self, path):
        d = Diagram(self.w, self.h)
        top = 16
        if self.title:
            d.group(24, top, self.w - 48, self.h - 32, self.title, self.title_icon)
            top += 56
        else:
            d.back.append(f'<rect x="24" y="{top}" width="{self.w-48}" height="{self.h-32}" rx="20" fill="#f7f3f4"/>')
            top += 16
        self.n = 0
        self._rows(d, self.rows, top, 0)
        d.save(path)

    def _chip(self, d, x, y, name):
        label, icon, outside = self.actors[name]
        fill = NEUTRAL if outside else TINT
        d.front.append(f'<rect x="{x}" y="{y}" width="{CHIP_W}" height="{CHIP_H}" rx="12" fill="{fill}"/>')
        if icon:
            color = MUTED if outside or not icon.startswith("lucide-") else MAGENTA
            d.front.append(f'<image href="{icon_uri(icon, color)}" x="{x+10}" y="{y+9}" width="26" height="26"/>')
        d._fits(label, 18, 600, CHIP_W - 56)
        d.front.append(f'<text x="{x+44}" y="{y+CHIP_H/2+6}" font-size="18" font-weight="600" fill="{INK}">{esc(label)}</text>')

    def _rows(self, d, rows, y, indent):
        x0 = 48 + indent
        for i, r in enumerate(rows):
            kind = r[0]
            if kind in ("msg", "reply"):
                _, src, dst, text = r
                lines = wrap(text, self._text_width(indent))
                h = max(ROW_MIN, len(lines) * LINE + 28)
                cy = y + h / 2
                self.n += 1
                d.front.append(f'<circle cx="{x0+18}" cy="{cy}" r="17" fill="{MAGENTA}"/>'
                               f'<text x="{x0+18}" y="{cy+7}" text-anchor="middle" font-size="18" font-weight="600" fill="#ffffff">{self.n}</text>')
                sx = x0 + 52
                self._chip(d, sx, cy - CHIP_H / 2, src)
                dx = sx + CHIP_W + 44
                if dst == src:
                    d.front.append(f'<text x="{dx+8}" y="{cy+6}" font-size="18" fill="{MUTED}" font-style="italic">itself</text>')
                else:
                    d.arrow([(sx + CHIP_W + 6, cy), (dx - GAP, cy)], dot=False, dashed=(kind == "reply"))
                    self._chip(d, dx, cy - CHIP_H / 2, dst)
                tx = self.text_x + indent
                ty = cy - (len(lines) - 1) * LINE / 2 + 7
                for k, line in enumerate(lines):
                    d.front.append(f'<text x="{tx}" y="{ty+k*LINE}" font-size="{TEXT}" fill="{INK}">{esc(line)}</text>')
                if i < len(rows) - 1 and rows[i + 1][0] in ("msg", "reply"):
                    d.back.append(f'<line x1="{x0}" y1="{y+h}" x2="{self.w-48-indent}" y2="{y+h}" stroke="{HAIR}" stroke-width="1.5"/>')
                y += h
            elif kind == "note":
                lines = wrap(r[1], self.w - 48 - 96 - indent - 32)
                h = len(lines) * LINE + 36
                d.front.append(f'<rect x="{x0+52}" y="{y+6}" width="{self.w-48-x0-52-24}" height="{h}" rx="12" fill="#ffffff" '
                               f'stroke="{DOTTED}" stroke-width="2" stroke-dasharray="2 8" stroke-linecap="round"/>')
                for k, line in enumerate(lines):
                    d.front.append(f'<text x="{x0+76}" y="{y+6+30+k*LINE}" font-size="19" fill="{MUTED}">{esc(line)}</text>')
                y += h + 12
            elif kind == "loop":
                inner_h = self._height(r[2], indent + 24)
                d.group(x0 - 8, y + 6, self.w - 48 - x0 - 16 + 8, 56 + inner_h + 18, r[1], "lucide-repeat", inner=True)
                self._rows(d, r[2], y + 56, indent + 24)
                y += 56 + inner_h + 24 + 12
        return y
