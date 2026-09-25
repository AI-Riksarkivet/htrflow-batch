import xml.etree.ElementTree as ET

import pytest

from htrflow_batch import quality

NS = "http://www.loc.gov/standards/alto/ns-v4#"


def _alto(pc: str | None, ns: bool = True) -> ET.Element:
    attr = "" if pc is None else f' PC="{pc}"'
    xmlns = f' xmlns="{NS}"' if ns else ""
    return ET.fromstring(
        f'<alto{xmlns}><Layout><Page WIDTH="10" HEIGHT="10"{attr}/></Layout></alto>'
    )


@pytest.mark.parametrize("ns", [True, False])
def test_the_score_is_page_pc(ns):
    assert quality.parse_alto_quality(_alto("0.8731", ns)) == pytest.approx(0.8731)


@pytest.mark.parametrize("pc", [None, "", "nan", "inf", "-0.1", "1.5", "high"])
def test_anything_but_a_number_in_0_1_is_no_score(pc):
    assert quality.parse_alto_quality(_alto(pc)) is None


def test_the_edges_are_scores():
    assert quality.parse_alto_quality(_alto("0")) == 0.0
    assert quality.parse_alto_quality(_alto("1")) == 1.0


PIPELINE = """steps:
- step: TextRecognition
  settings: {model: TrOCR, model_settings: {model: o/t}}
- step: QualityPrediction
  settings:
    model_settings:
      model: org/qp-model
      revision: 0123456789abcdef0123456789abcdef01234567
      model_file: model.joblib
      bin_config_file: bins.json
"""


def test_the_model_is_read_off_the_pipeline():
    assert quality.qp_model(PIPELINE) == (
        "org/qp-model",
        "0123456789abcdef0123456789abcdef01234567",
    )


@pytest.mark.parametrize("text", ["steps: []\n", "not: [valid", "", "steps: 3\n"])
def test_a_pipeline_without_the_step_names_no_model(text):
    assert quality.qp_model(text) == (None, None)


def test_no_scores_is_no_summary():
    assert quality.summary({}, PIPELINE, ["0001"]) is None


def test_the_summary_field_for_field():
    scores = {"0001": 0.9, "0002": 0.41234567, "0003": 0.7, "0004": 0.41234567}
    body = quality.summary(scores, PIPELINE, ["0001", "0002", "0004"])
    assert body == {
        "target": "bow_f1",
        "model": "org/qp-model",
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "mean": 0.6062,
        "min": 0.4123,
        "scored": 4,
        # ascending, ties by page name; canvas = index in iiif.json's items,
        # None for a page that has no canvas there
        "lowest": [
            {"page": "0002", "quality": 0.4123, "canvas": 1},
            {"page": "0004", "quality": 0.4123, "canvas": 2},
            {"page": "0003", "quality": 0.7, "canvas": None},
            {"page": "0001", "quality": 0.9, "canvas": 0},
        ],
    }


def test_lowest_keeps_five():
    scores = {f"{i:04d}": i / 10 for i in range(1, 10)}
    body = quality.summary(scores, PIPELINE, [])
    assert [e["page"] for e in body["lowest"]] == [
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
    ]
