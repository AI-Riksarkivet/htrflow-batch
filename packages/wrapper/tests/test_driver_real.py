"""Level 0 htrflow library-API pin (audit T4): the real ``Pipeline.from_config``,
``Export``, ``auto_import`` and ``Pipeline.run`` on a one-page CPU fixture,
inside the wrapper image. No model is loaded — a ``Binarization`` step
exercises the whole step/document/serializer path without HF Hub — so this
runs offline in a few seconds and pins exactly the surface ``driver.py``
depends on. Skipped wherever htrflow is not installed; run it with
``make test-driver-real`` (local image) or ``dagger call test-driver``.

Self-contained on purpose: it is mounted alone into the image, without the
package's conftest (moto is not installed there).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytestmark = pytest.mark.htrflow
htrflow = pytest.importorskip("htrflow")

from htrflow_batch.driver import (  # noqa: E402  (after importorskip)
    EXPECTED_FORMATS,
    htrflow_version,
    load_pipeline,
    process_page,
)


@pytest.fixture
def page(tmp_path: Path) -> Path:
    """One small grayscale JPEG with a dark block: enough for every step to
    have something to look at."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    ImageDraw.Draw(img).rectangle((60, 100, 540, 180), fill=0)
    path = tmp_path / "0001.jpg"
    img.save(path, "JPEG")
    return path


@pytest.fixture
def pipeline_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "pipeline.yaml"
    path.write_text("steps:\n  - step: Binarization\n")
    return path


def test_from_config_appends_exports_and_runs_one_page(
    tmp_path, page, pipeline_yaml, monkeypatch
):
    monkeypatch.chdir(tmp_path)  # Binarization writes ./binarized
    out_dir = tmp_path / "outputs"
    pipeline = load_pipeline(str(pipeline_yaml), out_dir)
    assert [type(s).__name__ for s in pipeline.steps] == [
        "Binarization",
        "Export",
        "Export",
    ]

    files = process_page(pipeline, page, out_dir)
    assert set(files) == set(EXPECTED_FORMATS)
    # store.upload_page parses both before the first PUT (W3): they must be
    # well-formed XML of the expected dialect even for a page with no text.
    alto = ET.fromstring(files["alto"].read_bytes())
    assert alto.tag.endswith("alto")
    pagexml = ET.fromstring(files["page"].read_bytes())
    assert pagexml.tag.endswith("PcGts")


def test_from_config_takes_a_path_and_rejects_export_steps(tmp_path):
    """The driver's TypeError fallback is for older builds; the pinned build
    takes a path. And a user-supplied Export must be refused, not doubled."""
    from htrflow.pipeline.pipeline import Pipeline

    path = tmp_path / "p.yaml"
    path.write_text("steps:\n  - step: Binarization\n")
    assert len(Pipeline.from_config(str(path)).steps) == 1

    path.write_text("steps:\n  - step: Export\n    settings: {dest: x, format: alto}\n")
    with pytest.raises(ValueError, match="must not contain Export"):
        load_pipeline(str(path), tmp_path / "out")


def test_unknown_step_and_model_class_raise_what_the_driver_maps_to_exit_13(tmp_path):
    """driver.load_pipeline turns KeyError (unknown step) and
    NotImplementedError (unknown model class) into ValueError -> exit 13;
    htrflow must keep raising exactly those."""
    from htrflow.pipeline.pipeline import Pipeline

    bad_step = tmp_path / "step.yaml"
    bad_step.write_text("steps:\n  - step: Segmentatoin\n")
    with pytest.raises(KeyError):
        Pipeline.from_config(str(bad_step))
    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(bad_step), tmp_path / "out")

    bad_model = tmp_path / "model.yaml"
    bad_model.write_text(
        "steps:\n  - step: Segmentation\n    settings:\n      model: NoSuchModel\n"
    )
    with pytest.raises(NotImplementedError):
        Pipeline.from_config(str(bad_model))
    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(bad_model), tmp_path / "out")


def test_step_registry_carries_the_steps_the_pipelines_use():
    from htrflow.pipeline.steps import STEPS, Export, auto_import

    assert {"segmentation", "textrecognition", "export", "binarization"} <= set(STEPS)
    assert STEPS["export"] is Export
    assert callable(auto_import)


def test_htrflow_version_is_known():
    assert htrflow_version() != "unknown"
