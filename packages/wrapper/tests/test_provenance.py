"""Each ALTO leaves the wrapper saying that htrflow-batch produced it, from
which image and on which htrflow base — a second ``<Processing>`` block next
to htrflow's own (docs: wrapper, "Provenance")."""

import xml.etree.ElementTree as ET
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import pytest

from htrflow_batch import provenance

NS = "http://www.loc.gov/standards/alto/ns-v4#"
ALTO = f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="{NS}"
      xsi:schemaLocation="{NS} http://www.loc.gov/standards/alto/v4/alto-4-4.xsd">
    <Description>
        <MeasurementUnit>pixel</MeasurementUnit>
        <sourceImageInformation>
            <fileName>0001.jpg</fileName>
        </sourceImageInformation>
        <Processing ID="processing">
            <processingDateTime>2026-09-07T09:00:00+00:00</processingDateTime>
            <processingStepDescription>TextRecognition(model=TrOCR)</processingStepDescription>
            <processingSoftware>
                <softwareName>htrflow</softwareName>
                <softwareVersion>0.2.6</softwareVersion>
            </processingSoftware>
        </Processing>
    </Description>
    <Layout>
        <Page WIDTH="2500" HEIGHT="3500" PHYSICAL_IMG_NR="0" ID="_0001.jpg">
            <PrintSpace/>
        </Page>
    </Layout>
</alto>
"""
IMAGE = "docker.io/riksarkivet/htrflow-batch@sha256:" + "a" * 64


def _stamped(tmp_path: Path) -> tuple[str, ET.Element]:
    path = tmp_path / "0001.xml"
    path.write_text(ALTO, encoding="utf-8")
    provenance.stamp_alto(path, image=IMAGE, base_revision="v0.2.6-35f48a7")
    text = path.read_text(encoding="utf-8")
    return text, ET.fromstring(text)


def _local(tag: str) -> str:
    return tag.split("}", 1)[1]


def test_stamp_appends_an_htrflow_batch_processing_block_last(tmp_path):
    _, root = _stamped(tmp_path)
    description = root.find(f"{{{NS}}}Description")
    assert [_local(c.tag) for c in description] == [
        "MeasurementUnit",
        "sourceImageInformation",
        "Processing",
        "Processing",
    ]
    block = description[-1]
    assert block.get("ID") == "htrflow-batch"
    assert [_local(c.tag) for c in block] == [
        "processingDateTime",
        "processingStepDescription",
        "processingStepDescription",
        "processingSoftware",
    ]
    datetime.fromisoformat(block[0].text)
    assert [e.text for e in block.findall(f"{{{NS}}}processingStepDescription")] == [
        f"image={IMAGE}",
        "htrflow-base=v0.2.6-35f48a7",
    ]
    software = {
        _local(c.tag): c.text for c in block.find(f"{{{NS}}}processingSoftware")
    }
    assert software["softwareName"] == "htrflow-batch-wrapper"
    assert software["softwareVersion"] == version("htrflow-batch-wrapper")
    assert software["softwareCreator"]
    assert software["applicationDescription"]


def test_stamp_leaves_htrflow_s_block_and_the_layout_untouched(tmp_path):
    before = ET.fromstring(ALTO)
    _, after = _stamped(tmp_path)
    for xpath in (
        f"{{{NS}}}Description/{{{NS}}}Processing[@ID='processing']",
        f"{{{NS}}}Layout",
    ):
        a, b = after.find(xpath), before.find(xpath)
        a.tail = b.tail = None  # only the whitespace after the element may move
        assert ET.tostring(a) == ET.tostring(b)


def test_stamp_keeps_the_declaration_and_the_default_namespace(tmp_path):
    text, _ = _stamped(tmp_path)
    assert text.startswith("<?xml version=")
    assert f'xmlns="{NS}"' in text
    assert "ns0:" not in text
    assert 'xsi:schemaLocation="' in text


def test_stamp_rejects_an_alto_that_is_not_xml(tmp_path):
    path = tmp_path / "0001.xml"
    path.write_text("<alto><Description>", encoding="utf-8")
    with pytest.raises(ValueError, match=r"0001\.xml is not well-formed XML"):
        provenance.stamp_alto(path, image=IMAGE, base_revision="x")
    assert path.read_text(encoding="utf-8") == "<alto><Description>"


def test_stamp_rejects_an_alto_without_a_description(tmp_path):
    path = tmp_path / "0001.xml"
    path.write_text(f'<alto xmlns="{NS}"><Layout/></alto>', encoding="utf-8")
    with pytest.raises(ValueError, match=r"0001\.xml has no Description element"):
        provenance.stamp_alto(path, image=IMAGE, base_revision="x")


def test_stamp_twice_leaves_one_htrflow_batch_block(tmp_path):
    """`ID` is an xsd:ID: a second block with the same ID would make the
    file schema-invalid, so a re-stamp is a no-op."""
    path = tmp_path / "0001.xml"
    path.write_text(ALTO, encoding="utf-8")
    provenance.stamp_alto(path, image=IMAGE, base_revision="x")
    once = path.read_text(encoding="utf-8")
    provenance.stamp_alto(path, image=IMAGE, base_revision="x")
    assert path.read_text(encoding="utf-8") == once
    assert once.count('ID="htrflow-batch"') == 1
