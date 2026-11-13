"""ALTO rewriting for `htrflow-campaigns export`: pure functions on bytes."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_SCHEMA_LOCATION = f"{ALTO_NS} http://www.loc.gov/standards/alto/v4/alto-4-4.xsd"
_ASPECT_TOLERANCE = 0.01
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_BOX = ("HPOS", "VPOS", "WIDTH", "HEIGHT")
_SLACK = 1  # px a word may reach past its line and still count as in it
_Axis = Callable[[float], float]
_Box = tuple[float, float, float, float]  # HPOS, VPOS, WIDTH, HEIGHT

ET.register_namespace("", ALTO_NS)
ET.register_namespace("xsi", _XSI_NS)


class AltoMismatch(ValueError):
    """The ALTO's page does not have the shape of the scan it is published with."""


def _q(name: str) -> str:
    return f"{{{ALTO_NS}}}{name}"


def _serialise(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def _map_points(points: str, fx: _Axis, fy: _Axis) -> str:
    """Rewrite an `x,y x,y` or `x y x y` list number by number, keeping its
    separators: ``fx`` on every x, ``fy`` on every y."""
    count = 0

    def one(match: re.Match[str]) -> str:
        nonlocal count
        axis = fx if count % 2 == 0 else fy
        count += 1
        return str(axis(float(match.group())))

    return _NUMBER.sub(one, points)


def _corner(points: str) -> tuple[float, float] | None:
    """The top-left corner of an outline's bounding box."""
    numbers = [float(n) for n in _NUMBER.findall(points)]
    if len(numbers) < 2 or len(numbers) % 2:
        return None
    return min(numbers[0::2]), min(numbers[1::2])


def _box(el: ET.Element) -> _Box | None:
    """The element's box, or None when it does not state all four numbers."""
    if any(name not in el.attrib for name in _BOX):
        return None
    x, y, w, h = (float(el.attrib[name]) for name in _BOX)
    return x, y, w, h


def _within(box: _Box, outer: _Box, dx: float = 0, dy: float = 0) -> bool:
    """Whether ``box``, moved by ``(dx, dy)``, lies in ``outer``."""
    x, y, w, h = box
    ox, oy, ow, oh = outer
    return (
        ox - _SLACK <= x + dx
        and x + dx + w <= ox + ow + _SLACK
        and oy - _SLACK <= y + dy
        and y + dy + h <= oy + oh + _SLACK
    )


def _to_page(root: ET.Element) -> None:
    """Put every word and every block outline where it is on the page.

    htrflow's ALTO template gets two positions wrong, both by the corner of
    the ``TextBlock`` they belong to. A word's box is counted from that
    corner, not from the page's, so a search hit is drawn up and to the left
    of its word. A block's outline is moved by that corner once more, down
    and to the right, often off the page. Blocks' and lines' own boxes, and
    lines' outlines, are right.

    A file does not say which form it has; its geometry does. An outline
    belongs on its block: one whose corner is exactly twice the block's is
    moved back. A word lies in its line: the page is read both ways, and its
    words are moved by their blocks' corners only when that puts more of
    them in their lines than leaving them does. So a file that is already
    right is left alone.
    """
    words: list[tuple[ET.Element, float, float]] = []
    as_written = moved = 0
    for block in root.iter(_q("TextBlock")):
        corner = _box(block)
        if corner is None:
            continue
        bx, by = corner[:2]
        outline = block.find(f"{_q('Shape')}/{_q('Polygon')}")
        if outline is not None:
            points = outline.get("POINTS", "")
            if _corner(points) == (2 * bx, 2 * by):
                back = _map_points(points, lambda x: x - bx, lambda y: y - by)
                outline.set("POINTS", back)
        for line in block.iter(_q("TextLine")):
            line_box = _box(line)
            for word in line.iter(_q("String")):
                box = _box(word)
                if line_box is None or box is None:
                    continue
                words.append((word, bx, by))
                as_written += _within(box, line_box)
                moved += _within(box, line_box, bx, by)
    if moved > as_written:
        for word, bx, by in words:
            word.set("HPOS", str(float(word.attrib["HPOS"]) + bx))
            word.set("VPOS", str(float(word.attrib["VPOS"]) + by))


def scale_alto(xml: bytes, native: tuple[int, int], stem: str, number: int) -> bytes:
    """Scale every coordinate to the native scan size and name the page."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise AltoMismatch(f"the ALTO is not well-formed XML: {exc}") from exc
    page = root.find(f".//{_q('Page')}")
    if page is None:
        raise AltoMismatch("the ALTO has no Page")
    try:
        width, height = float(page.attrib["WIDTH"]), float(page.attrib["HEIGHT"])
    except (KeyError, ValueError) as exc:
        raise AltoMismatch("the ALTO's Page has no usable WIDTH and HEIGHT") from exc
    native_w, native_h = native
    if width <= 0 or height <= 0:
        raise AltoMismatch("the ALTO's Page has no size")
    if abs((width / height) / (native_w / native_h) - 1) > _ASPECT_TOLERANCE:
        raise AltoMismatch(
            f"the ALTO is {width:g}x{height:g} but the scan is {native_w}x{native_h}"
        )
    sx, sy = native_w / width, native_h / height

    _to_page(root)
    for el in root.iter():
        for names, factor in ((("HPOS", "WIDTH"), sx), (("VPOS", "HEIGHT"), sy)):
            for name in names:
                if name in el.attrib:
                    el.set(name, str(round(float(el.attrib[name]) * factor)))
        if el.tag == _q("Polygon") and "POINTS" in el.attrib:
            scaled = _map_points(
                el.attrib["POINTS"], lambda x: round(x * sx), lambda y: round(y * sy)
            )
            el.set("POINTS", scaled)

    page.set("WIDTH", str(native_w))
    page.set("HEIGHT", str(native_h))
    page.set("PHYSICAL_IMG_NR", str(number))
    page.set("ID", f"_{number:05d}")  # the image number, not the run's page name
    _file_name(root).text = stem
    return _serialise(root)


def _child(parent: ET.Element, name: str, index: int) -> ET.Element:
    """``parent``'s ``name`` child, inserted at ``index`` if it has none."""
    found = parent.find(_q(name))
    if found is None:
        found = ET.Element(_q(name))
        parent.insert(index, found)
    return found


def _file_name(root: ET.Element) -> ET.Element:
    """``Description/sourceImageInformation/fileName``, created where ALTO 4.4
    puts each one if absent: Description first in the document, then
    MeasurementUnit, sourceImageInformation, and fileName first in that."""
    description = root.find(_q("Description"))
    if description is None:
        description = _child(root, "Description", 0)
        _child(description, "MeasurementUnit", 0).text = "pixel"
    after_unit = 1 if description.find(_q("MeasurementUnit")) is not None else 0
    info = _child(description, "sourceImageInformation", after_unit)
    return _child(info, "fileName", 0)


def empty_alto(native: tuple[int, int], stem: str, number: int) -> bytes:
    """An ALTO page with no text, at the scan's native size."""
    width, height = native
    root = ET.Element(_q("alto"), {f"{{{_XSI_NS}}}schemaLocation": _SCHEMA_LOCATION})
    description = ET.SubElement(root, _q("Description"))
    ET.SubElement(description, _q("MeasurementUnit")).text = "pixel"
    info = ET.SubElement(description, _q("sourceImageInformation"))
    ET.SubElement(info, _q("fileName")).text = stem
    layout = ET.SubElement(root, _q("Layout"))
    page = ET.SubElement(
        layout,
        _q("Page"),
        {
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "PHYSICAL_IMG_NR": str(number),
            "ID": f"_{number:05d}",
        },
    )
    ET.SubElement(
        page,
        _q("PrintSpace"),
        {"HPOS": "0", "VPOS": "0", "WIDTH": str(width), "HEIGHT": str(height)},
    )
    return _serialise(root)
