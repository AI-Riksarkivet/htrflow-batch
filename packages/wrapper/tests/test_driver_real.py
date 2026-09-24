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
    """The pinned build takes a path, which is all the driver hands it. And a
    user-supplied Export must be refused, not doubled."""
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
    from htrflow.pipeline import pipeline as pipeline_module
    from htrflow.pipeline.steps import STEPS, Export, auto_import

    assert {"segmentation", "textrecognition", "export", "binarization"} <= set(STEPS)
    assert STEPS["export"] is Export
    assert callable(auto_import)
    # driver._tracked_steps swaps this name to reach the steps a failed
    # construction built; without it a failure leaks their weights silently
    assert callable(getattr(pipeline_module, "init_step", None))


def test_htrflow_version_is_known():
    assert htrflow_version() != "unknown"


REGISTRIES = ("_tasks", "_exports", "_steps")


def test_progress_registries_are_empty_after_every_page(
    tmp_path, page, pipeline_yaml, monkeypatch
):
    """X2: htrflow's ``progress`` keeps module-global dicts keyed by Document
    and pops none of them, so a long-lived Pipeline over a whole volume
    retains every page's Region tree. driver.release_documents empties them
    once a page is done; this pins the names it reaches for against the real
    module and proves the footprint is flat across pages, not merely
    smaller."""
    from htrflow import progress

    assert set(REGISTRIES) <= set(vars(progress))
    monkeypatch.chdir(tmp_path)  # Binarization writes ./binarized
    out_dir = tmp_path / "outputs"
    pipeline = load_pipeline(str(pipeline_yaml), out_dir)

    def held():
        return [len(getattr(progress, name)) for name in REGISTRIES] + [
            len(progress._progress.tasks)
        ]

    for i in range(3):
        image = tmp_path / f"{i:04d}.jpg"
        image.write_bytes(page.read_bytes())
        assert set(process_page(pipeline, image, out_dir)) == set(EXPECTED_FORMATS)
        assert held() == [0, 0, 0, 0], "the progress registries kept the page"


class _BrokenModel:
    """A model that raises the way ultralytics/yolo.py did on 2026-09-08
    (a detection with no polygon: ``TypeError: 'NoneType' object is not
    iterable``). Its metadata is what StepMetadata carries the model id in."""

    metadata = {"model": "broken-test-model"}

    def __call__(self, images, **kwargs):
        raise TypeError("'NoneType' object is not iterable")


def test_a_step_whose_worker_thread_dies_fails_the_page_instead_of_hanging(
    tmp_path, page
):
    """B88, against the real threading: htrflow's Inference hands its batch to
    a daemon thread and waits on a Future, so a model exception kills the
    thread and ``Pipeline.run`` waits for a future nobody will complete. Only
    driver's guard ends this -- without it the assertion below never runs and
    the pod holds its GPU to the deadline."""
    from htrflow.pipeline.pipeline import Pipeline
    from htrflow.pipeline.steps import Segmentation

    from htrflow_batch.driver import PipelineDead

    pipeline = Pipeline([Segmentation(_BrokenModel())])
    with pytest.raises(PipelineDead) as excinfo:
        process_page(pipeline, page, tmp_path / "outputs")
    assert str(excinfo.value) == (
        f"page {page.stem}: htrflow's Segmentation (model broken-test-model) "
        "worker thread died; the page is marked failed and the pipeline is rebuilt"
    )


@pytest.fixture(autouse=True)
def interpreter_thread_hook(monkeypatch):
    """The interpreter's own thread excepthook, as in the pod: pytest's
    reports even the SystemExit a stopped htrflow worker ends on."""
    import threading

    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)


class _SlowModel:
    """A model that answers each line after ``gap`` seconds, or waits on
    ``hold`` first when one is given: a model on a big page, or a hung one."""

    metadata = {"model": "slow-test-model"}

    def __init__(self, gap: float = 0.0, hold=None):
        self.gap, self.hold = gap, hold

    def __call__(self, images, **kwargs):
        import time

        if self.hold is not None:
            self.hold.wait(60)
        time.sleep(self.gap)
        return [[] for _ in images]


def test_releasing_real_inference_steps_ends_their_threads(caplog):
    """Review M-5: ``_stop_threads`` relies on htrflow's own names
    (``_thread``, ``_queue._thread``, ``_queue._in``, ``_queue._out``); this
    pins them against the real Inference and BatchedQueue, and that the stop
    ends both threads without a word at ERROR."""
    from htrflow.pipeline.steps import TextRecognition

    from htrflow_batch import driver

    steps = [TextRecognition(_SlowModel()), TextRecognition(_SlowModel())]
    threads = [t for s in steps for t in (s._thread, s._queue._thread)]
    with caplog.at_level("ERROR"):
        driver.release_steps(steps)
    assert caplog.text == ""
    assert driver.leaked_threads(grace=5.0) == 0
    assert not any(t.is_alive() for t in threads)


def test_the_watchdog_sees_a_real_inference_step_move():
    """Review I-2: every batch the model finishes changes what
    ``_progress_mark`` reads off the real BatchedQueue."""
    import time

    from htrflow.pipeline.steps import TextRecognition

    from htrflow_batch import driver

    step = TextRecognition(_SlowModel(gap=0.05))
    pipeline = type("P", (), {"steps": [step]})()
    futures = [step._queue.put(i) for i in range(20)]
    marks = set()
    while not all(f.done() for f in futures):
        marks.add(driver._progress_mark(pipeline))
        time.sleep(0.01)
    driver.release_steps([step])
    assert len(marks) > 5


def test_a_dead_real_pipeline_exports_nothing_late(tmp_path, page):
    """Review I-3, against htrflow's own Pipeline.run and Export: once the
    page is given up on and the stalled model returns, the helper must not
    go on to the Exports -- no file for a page already failed, nothing in
    htrflow's progress registry."""
    import threading
    import time

    from htrflow import progress
    from htrflow.pipeline.pipeline import Pipeline
    from htrflow.pipeline.steps import Export, TextRecognition

    from htrflow_batch import driver

    hold = threading.Event()
    out = tmp_path / "outputs"
    step = TextRecognition(_SlowModel(hold=hold))
    pipeline = Pipeline(
        [step, Export(str(out / "alto"), "alto"), Export(str(out / "page"), "page")]
    )
    with pytest.MonkeyPatch.context() as m:
        m.setattr(driver, "THREAD_POLL_SECONDS", 0.05)
        with pytest.raises(driver.PipelineDead, match="no progress"):
            process_page(pipeline, page, out, seconds=0.5)
    driver.release_pipeline(pipeline)
    hold.set()
    time.sleep(1.0)  # the model returns; a live helper would export now
    assert not list(out.rglob("*.xml"))
    assert (progress._exports, progress._steps) == ({}, {})


class _Boxes:
    """A segmentation model as htrflow drives one: per image, the Regions
    it found -- two boxes, each attached as a child of the node it ran on."""

    metadata = {"model": "boxes-test-model"}

    def __call__(self, images, **kwargs):
        from htrflow.document import Region
        from htrflow.utils.geometry import Bbox

        return [
            [
                Region(Bbox(10, 10, 200, 60).polygon()),
                Region(Bbox(10, 70, 200, 120).polygon()),
            ]
            for _ in images
        ]


class _Reader:
    """A text recognition model: one Text per image it is handed."""

    metadata = {"model": "reader-test-model"}

    def __init__(self):
        self.read = 0

    def __call__(self, images, **kwargs):
        from htrflow.document import Text

        out = []
        for _ in images:
            self.read += 1
            out.append([Text(f"line {self.read}", 0.9)])
        return out


@pytest.mark.parametrize("levels", [1, 2])
def test_the_export_check_against_htrflows_own_steps_and_serializers(
    tmp_path, page, levels
):
    """Pins the upstream behaviour the export check exists for: through
    htrflow's real Segmentation/TextRecognition steps, Export and ALTO/PAGE
    templates, one segmentation level puts the lines on the page and the
    files come out without their text -- the page fails, with the cause --
    while region then line exports it. When htrflow writes flat lines, the
    one-level case stops failing: this test says so, and the check (and the
    converter's refusal of the shape) can be retired."""
    from htrflow.pipeline.pipeline import Pipeline
    from htrflow.pipeline.steps import Export, Segmentation, TextRecognition

    from htrflow_batch import driver
    from htrflow_batch.exportcheck import TextNotExported

    out = tmp_path / "outputs"
    steps = [Segmentation(_Boxes()) for _ in range(levels)] + [
        TextRecognition(_Reader())
    ]
    pipeline = Pipeline(
        steps + [Export(str(out / "alto"), "alto"), Export(str(out / "page"), "page")]
    )
    try:
        if levels == 1:
            with pytest.raises(TextNotExported, match="recognized 2 lines") as caught:
                process_page(pipeline, page, out)
            assert "directly on the page" in str(caught.value)
            assert not list(out.rglob("*.xml"))
        else:
            files = process_page(pipeline, page, out)
            alto = ET.fromstring(files["alto"].read_bytes())
            contents = [
                e.get("CONTENT") for e in alto.iter() if e.tag.endswith("String")
            ]
            assert len(contents) == 4 and all(contents)
    finally:
        driver.release_steps(steps)
