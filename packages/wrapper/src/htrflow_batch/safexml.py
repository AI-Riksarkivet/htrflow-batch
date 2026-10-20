"""Every XML document the wrapper parses comes through here.

ALTO and PAGE never carry a DTD, so one is refused outright: that closes
entity expansion and external entities however old the expat the image's
Python links against (Ubuntu 22.04's reports 2.4.7). A refusal is raised as
``ET.ParseError``, so it takes each caller's existing "not well-formed" path
-- the page fails as malformed, or the stored file is left out -- instead of
a new exception type that path does not catch.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as _defused


def fromstring(data: bytes) -> ET.Element:
    try:
        return _defused.fromstring(data, forbid_dtd=True)
    except DefusedXmlException as e:
        raise ET.ParseError(f"refused: {e}") from e


# Typed as the stdlib's ET.parse is: a parsed document always has a root.
def parse(source: Path) -> ET.ElementTree[ET.Element]:
    try:
        return _defused.parse(source, forbid_dtd=True)
    except DefusedXmlException as e:
        raise ET.ParseError(f"refused: {e}") from e
