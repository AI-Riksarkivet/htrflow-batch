"""Provenance: every ALTO says that htrflow-batch produced it (docs: wrapper).

htrflow's own ``<Processing>`` block names htrflow, its version, the steps
and each model's commit. What it cannot know is the layer above: which
htrflow-batch, from which image, on which htrflow base. That goes into a
second ``<Processing ID="htrflow-batch">`` block appended to
``<Description>`` — ALTO 4.4 allows any number of them, in that position —
after htrflow's Export wrote the file and before it is uploaded. htrflow's
block and the rest of the file are left as written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"
ET.register_namespace("", ALTO_NS)
ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    e = ET.SubElement(parent, f"{{{ALTO_NS}}}{tag}")
    e.text = text
    return e


def processing_block(image: str, base_revision: str) -> ET.Element:
    """The block itself; the software fields are the installed package's own
    metadata, the same way htrflow fills in its block."""
    meta = metadata.metadata("htrflow-batch-wrapper")
    block = ET.Element(f"{{{ALTO_NS}}}Processing", ID="htrflow-batch")
    _sub(block, "processingDateTime", datetime.now(timezone.utc).isoformat())
    _sub(block, "processingStepDescription", f"image={image}")
    _sub(block, "processingStepDescription", f"htrflow-base={base_revision}")
    software = _sub(block, "processingSoftware")
    _sub(software, "softwareCreator", meta["Author"])
    _sub(software, "softwareName", meta["Name"])
    _sub(software, "softwareVersion", meta["Version"])
    _sub(software, "applicationDescription", meta["Summary"])
    ET.indent(block, space="    ", level=2)
    block.tail = "\n    "
    return block


def stamp_alto(path: Path, *, image: str, base_revision: str) -> None:
    """Append the block to the ALTO at ``path``, in place. An ALTO that will
    not parse is not a usable output: raise, and the page fails."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"{path.name} is not well-formed XML ({e}).") from e
    description = tree.getroot().find(f"{{{ALTO_NS}}}Description")
    if description is None:
        raise ValueError(
            f"{path.name} has no Description element to record provenance in."
        )
    if description.find(f"{{{ALTO_NS}}}Processing[@ID='htrflow-batch']") is not None:
        return  # already stamped; ID is an xsd:ID, a twin would break the schema
    if len(description):
        description[-1].tail = "\n        "
    description.append(processing_block(image, base_revision))
    tree.write(path, encoding="UTF-8", xml_declaration=True)
