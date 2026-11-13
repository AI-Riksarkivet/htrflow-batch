"""ALTO rewriting for `htrflow-campaigns export`: pure functions on bytes."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_SCHEMA_LOCATION = f"{ALTO_NS} http://www.loc.gov/standards/alto/v4/alto-4-4.xsd"
_ASPECT_TOLERANCE = 0.01
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_Axis = Callable[[float], float]

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
