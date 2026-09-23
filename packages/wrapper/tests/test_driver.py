"""Tests for driver.py without requiring htrflow installed (import-guarded).

Fakes htrflow through the ``fake_htrflow`` fixture (conftest)."""

from __future__ import annotations

import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest


class _Export:
    def __init__(self, dest, fmt):
        self.dest, self.fmt = dest, fmt


def _inject_recording_fake_htrflow(fake_htrflow) -> list:
    """A fake htrflow whose ``from_config`` records every call: the list it
    returns is empty exactly when nothing was built -- no model loaded."""
    built: list = []

    class MockPipeline:
        def __init__(self, steps):
            self.steps = steps

        @staticmethod
        def from_config(config):
            built.append(config)
            return MockPipeline([])

    fake_htrflow(pipeline={"Pipeline": MockPipeline}, steps={"Export": _Export})
    return built


def _inject_raising_fake_htrflow(fake_htrflow, exc: BaseException) -> None:
    """A fake htrflow whose ``from_config`` raises ``exc`` part-way through."""

    def from_config(config):
        raise exc

    pipeline = type("Pipeline", (), {"from_config": staticmethod(from_config)})
    fake_htrflow(pipeline={"Pipeline": pipeline}, steps={"Export": _Export})


def test_load_pipeline_path_api(tmp_path, fake_htrflow):
    """from_config gets the path; the Export steps follow the built ones."""
    called_with = _inject_recording_fake_htrflow(fake_htrflow)
    out_dir = tmp_path / "output"
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    from htrflow_batch.driver import load_pipeline

    pipeline = load_pipeline(str(pipeline_yaml), out_dir)

    assert called_with == [str(pipeline_yaml)]
    assert [(s.fmt, s.dest) for s in pipeline.steps] == [
        ("alto", str(out_dir / "alto")),
        ("page", str(out_dir / "page")),
    ]


@pytest.mark.parametrize("name", ["Export", "export", "EXPORT"])
def test_export_steps_are_refused_before_any_model_loads(tmp_path, fake_htrflow, name):
    """3098: the rule is read off the YAML, before ``from_config`` builds a
    single step -- so the warm-up (which goes through build_pipeline too)
    refuses it, instead of passing and leaving every index to load all the
    weights onto a GPU just to exit 13. htrflow looks a step up by its
    lower-cased name, so any spelling is the Export step."""
    built = _inject_recording_fake_htrflow(fake_htrflow)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text(
        "steps:\n"
        "  - step: Segmentation\n"
        "    settings: {model: yolo, model_settings: {model: a/b}}\n"
        f"  - step: {name}\n"
        "    settings: {dest: out, format: alto}\n"
    )

    from htrflow_batch.driver import build_pipeline, load_pipeline

    for build in (build_pipeline, lambda p: load_pipeline(p, tmp_path / "out")):
        with pytest.raises(ValueError, match="must not contain Export steps"):
            build(str(pipeline_yaml))
    assert built == []


PIN = "7c44178d85926b4a096c55c89bf224855a201fbf"


@pytest.mark.parametrize(
    "settings",
    [
        # YOLO: pinned inside model_settings, unpinned by a key beside it
        f"{{model: yolo, model_settings: {{model: a/b, revision: {PIN}}}, "
        "revision: null}",
        # TrOCR: the same through model_kwargs, which the merge replaces whole
        "{model: TrOCR, model_settings: {model: a/b, model_kwargs: "
        f"{{revision: {PIN}}}}}, model_kwargs: {{}}}}",
        # a branch beside the pin is a moving target too
        f"{{model: yolo, model_settings: {{model: a/b, revision: {PIN}}}, "
        "revision: main}",
        # another commit beside the pin is not the revision that was reviewed
        f"{{model: yolo, model_settings: {{model: a/b, revision: {PIN}}}, "
        f"revision: {'0' * 40}}}",
        # audit 0923 S-3: TrOCR's processor (its tokenizer) is a second load
        # from the Hub, pinned through processor_kwargs, and undone the same way
        "{model: TrOCR, model_settings: {model: a/b, model_kwargs: "
        f"{{revision: {PIN}}}, processor_kwargs: {{revision: {PIN}}}}}, "
        "processor_kwargs: {}}",
        "{model: TrOCR, model_settings: {model: a/b, processor: c/d, "
        f"model_kwargs: {{revision: {PIN}}}, processor_kwargs: {{revision: {PIN}}}}}, "
        "processor_kwargs: {revision: main}}",
    ],
    ids=[
        "yolo-null",
        "trocr-empty-kwargs",
        "yolo-branch",
        "yolo-other-commit",
        "trocr-empty-processor-kwargs",
        "trocr-processor-branch",
    ],
)
def test_a_pin_overridden_beside_model_settings_is_refused(
    tmp_path, fake_htrflow, settings
):
    """3058: htrflow hands the model ``model_settings | <the other keys>``, so
    a key beside model_settings replaces the pin the policy and ``validate``
    read. The revision the model will actually get is checked, before a
    single weight is fetched, and the refusal is permanent (a ValueError)."""
    built = _inject_recording_fake_htrflow(fake_htrflow)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text(
        f"steps:\n  - step: Segmentation\n    settings: {settings}\n"
    )

    from htrflow_batch.driver import build_pipeline

    with pytest.raises(ValueError, match="does not load its pinned revision"):
        build_pipeline(str(pipeline_yaml))
    assert built == []


@pytest.mark.parametrize(
    "settings",
    [
        f"{{model: yolo, model_settings: {{model: a/b, revision: {PIN}}}}}",
        "{model: TrOCR, model_settings: {model: a/b, model_kwargs: "
        f"{{revision: {PIN}}}}}, generation_settings: {{batch_size: 2}}}}",
        # the processor pinned too, from a repo of its own
        "{model: TrOCR, model_settings: {model: a/b, processor: c/d, "
        f"model_kwargs: {{revision: {PIN}}}, processor_kwargs: {{revision: {PIN}}}}}}}",
        # Not pinned anywhere: whether that is allowed is the chart's
        # requireModelRevision, which admission enforces -- off by default.
        "{model: yolo, model_settings: {model: a/b}}",
    ],
    ids=[
        "yolo-pinned",
        "trocr-pinned",
        "trocr-processor-pinned",
        "unpinned-everywhere",
    ],
)
def test_a_pin_that_reaches_the_model_is_built(tmp_path, fake_htrflow, settings):
    built = _inject_recording_fake_htrflow(fake_htrflow)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text(
        f"steps:\n  - step: Segmentation\n    settings: {settings}\n"
    )

    from htrflow_batch.driver import build_pipeline

    build_pipeline(str(pipeline_yaml))
    assert built == [str(pipeline_yaml)]


def test_load_pipeline_malformed_yaml_is_permanent(tmp_path, fake_htrflow):
    """Malformed pipeline YAML must surface as ValueError (main.py's
    permanent/exit-13 bucket), not yaml.YAMLError (which main.py's bare
    `except Exception` would misclassify as transient/exit-1)."""
    _inject_recording_fake_htrflow(fake_htrflow)

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: [unclosed")

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(pipeline_yaml), out_dir)


def test_load_pipeline_missing_file_is_permanent(tmp_path, fake_htrflow):
    """A nonexistent pipeline path must also surface as ValueError, not
    FileNotFoundError."""
    _inject_recording_fake_htrflow(fake_htrflow)

    out_dir = tmp_path / "output"
    out_dir.mkdir()

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(tmp_path / "does-not-exist.yaml"), out_dir)


def test_load_pipeline_model_download_oserror_stays_transient(tmp_path, fake_htrflow):
    """from_config instantiates models (HF downloads); a network OSError
    there is retryable and must NOT be wrapped into ValueError (which
    main.py classifies permanent/exit-13). Final-review parked finding."""
    _inject_raising_fake_htrflow(
        fake_htrflow, OSError("We couldn't connect to 'https://huggingface.co'")
    )
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")  # valid YAML: config is fine

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(OSError, match="huggingface"):
        load_pipeline(str(pipeline_yaml), tmp_path / "output")


def _inject_process_fakes(fake_htrflow):
    """htrflow as process_page imports it: ``auto_import`` and no
    ``progress`` submodule."""
    fake_htrflow(steps={"auto_import": lambda paths: [object()]})


class _NoopPipeline:
    def run(self, document):
        pass


def test_process_page_returns_both_formats(tmp_path, fake_htrflow):
    _inject_process_fakes(fake_htrflow)
    out_dir = tmp_path / "outputs"
    for fmt in ("alto", "page"):
        (out_dir / fmt).mkdir(parents=True)
        (out_dir / fmt / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    files = process_page(_NoopPipeline(), image, out_dir)
    assert set(files) == {"alto", "page"}


def test_process_page_raises_when_a_format_is_missing(tmp_path, fake_htrflow):
    """W2: a page with ALTO but no PAGE XML must fail here, not be uploaded
    half-complete and later verified as done."""
    _inject_process_fakes(fake_htrflow)
    out_dir = tmp_path / "outputs"
    (out_dir / "alto").mkdir(parents=True)
    (out_dir / "alto" / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    with pytest.raises(RuntimeError, match="page"):
        process_page(_NoopPipeline(), image, out_dir)


@pytest.mark.parametrize(
    "exc",
    [
        KeyError("segmentatoin"),
        NotImplementedError("Model X is not supported"),
        TypeError("__init__() got an unexpected keyword argument 'batch_sz'"),
    ],
    ids=["unknown-step", "unknown-model", "mistyped-setting"],
)
def test_load_pipeline_unknown_step_or_model_is_permanent(tmp_path, fake_htrflow, exc):
    """htrflow raises KeyError for an unknown step name (STEPS[...]),
    NotImplementedError for an unknown model class, and TypeError from inside
    a step whose ``settings:`` it hands the constructor as keyword arguments
    (W2). All are config mistakes and must become ValueError (exit 13) at
    once, not a transient retry."""
    _inject_raising_fake_htrflow(fake_htrflow, exc)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: []")

    from htrflow_batch.driver import load_pipeline

    with pytest.raises(ValueError, match="bad pipeline config"):
        load_pipeline(str(pipeline_yaml), tmp_path / "out")


class _FakeProgress(ModuleType):
    """htrflow's ``progress`` module in miniature: three module-global dicts
    keyed by ``Document`` plus the rich progress singleton that hands out the
    task ids. Nothing in htrflow ever pops them."""

    def __init__(self):
        super().__init__("htrflow.progress")
        self._tasks, self._exports, self._steps = {}, {}, {}
        self.removed = []
        self._progress = SimpleNamespace(remove_task=self.removed.append)

    def register(self, document) -> None:
        """What Pipeline.run + Export do to a document on its way through."""
        self._tasks[document] = f"task-{len(self._tasks)}"
        self._steps[document] = ["Binarization", "Export"]
        self._exports[document] = ["outputs/alto/0001.xml"]


def _inject_progress_fake(monkeypatch) -> _FakeProgress:
    fake = _FakeProgress()
    monkeypatch.setitem(sys.modules, "htrflow.progress", fake)
    monkeypatch.setattr(sys.modules["htrflow"], "progress", fake, raising=False)
    return fake


def test_process_page_releases_the_document_from_htrflows_progress(
    tmp_path, monkeypatch, fake_htrflow
):
    """B-3/X2: htrflow's progress registries are module-global and never
    popped. Its CLI runs one process per volume; the wrapper runs one
    long-lived Pipeline over thousands of pages, so every page's Region tree
    would stay reachable until the process exits (~0.5 GB at 10 000 pages)."""
    _inject_process_fakes(fake_htrflow)
    progress = _inject_progress_fake(monkeypatch)

    class _RegisteringPipeline:
        def run(self, document):
            progress.register(document)
            return document

    out_dir = tmp_path / "outputs"
    for fmt in ("alto", "page"):
        (out_dir / fmt).mkdir(parents=True)
        (out_dir / fmt / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    files = process_page(_RegisteringPipeline(), image, out_dir)

    assert set(files) == {"alto", "page"}
    assert (progress._tasks, progress._steps, progress._exports) == ({}, {}, {})
    assert progress.removed == ["task-0"]  # the rich task goes too


def test_process_page_tolerates_an_htrflow_without_the_progress_module(
    tmp_path, fake_htrflow
):
    """The release is best-effort: an htrflow that never had those registries
    (or renames them) must still process pages."""
    _inject_process_fakes(fake_htrflow)  # fake htrflow, no `progress` submodule
    out_dir = tmp_path / "outputs"
    for fmt in ("alto", "page"):
        (out_dir / fmt).mkdir(parents=True)
        (out_dir / fmt / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    assert set(process_page(_NoopPipeline(), image, out_dir)) == {"alto", "page"}


class _HalfWritingPipeline:
    """A pipeline whose ALTO Export lands and whose PAGE Export does not."""

    def __init__(self, out_dir, fail=None):
        self.out_dir, self.fail = out_dir, fail
        self.stem = None

    def run(self, document):
        alto = self.out_dir / "alto" / f"{self.stem}.xml"
        alto.parent.mkdir(parents=True, exist_ok=True)
        alto.write_text("<x/>")
        if self.fail is not None:
            raise self.fail
        return document


def test_process_page_removes_what_a_failed_page_wrote(tmp_path, fake_htrflow):
    """X2: the workdir is memory-backed, and consume's rolling delete can only
    reach the files process_page RETURNS. A page that raises must take its
    half-written outputs with it, or every failed page leaks for the whole
    volume."""
    _inject_process_fakes(fake_htrflow)
    out_dir = tmp_path / "outputs"
    pipeline = _HalfWritingPipeline(out_dir)
    image = tmp_path / "x.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    for i in range(20):
        pipeline.stem = f"{i:04d}"
        image = tmp_path / f"{i:04d}.jpg"
        image.write_bytes(b"jpg")
        with pytest.raises(RuntimeError, match="no page output written"):
            process_page(pipeline, image, out_dir)
        # nothing but the (unrelated) input images the test itself wrote
        assert [p.name for p in out_dir.rglob("*") if p.is_file()] == []


def test_process_page_removes_what_a_raising_pipeline_wrote(tmp_path, fake_htrflow):
    """Same for a pipeline that dies between its two Export steps: the
    original exception propagates, but the ALTO it managed to write does not
    stay in tmpfs."""
    _inject_process_fakes(fake_htrflow)
    out_dir = tmp_path / "outputs"
    pipeline = _HalfWritingPipeline(out_dir, fail=RuntimeError("CUDA out of memory"))
    pipeline.stem = "0001"
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        process_page(pipeline, image, out_dir)
    assert [p for p in out_dir.rglob("*") if p.is_file()] == []


def test_process_page_releases_intermediate_documents_too(
    tmp_path, monkeypatch, fake_htrflow
):
    """Every ProcessImages-type step (Binarization, Blurring...) returns a NEW
    Document, so a pipeline with two of them registers three: the one handed
    in, the middle one, and the one run() gives back. Naming the two ends
    leaves the middle one in the registries forever."""
    _inject_process_fakes(fake_htrflow)
    progress = _inject_progress_fake(monkeypatch)

    class _TwoStagePipeline:
        def run(self, document):
            progress.register(document)  # step 1 sees what we handed in
            middle = object()
            progress.register(middle)  # step 2 sees step 1's new Document
            final = object()
            progress.register(final)  # progress.done() sees step 2's
            return final

    out_dir = tmp_path / "outputs"
    for fmt in ("alto", "page"):
        (out_dir / fmt).mkdir(parents=True)
        (out_dir / fmt / "0001.xml").write_text("<x/>")
    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    process_page(_TwoStagePipeline(), image, out_dir)

    assert (progress._tasks, progress._steps, progress._exports) == ({}, {}, {})
    assert progress.removed == ["task-0", "task-1", "task-2"]


def test_process_page_releases_a_failed_page_from_the_registries(
    tmp_path, monkeypatch, fake_htrflow
):
    """A page that raises registered documents too; if only the success path
    released them, a volume whose pages all fail leaks exactly as before."""
    _inject_process_fakes(fake_htrflow)
    progress = _inject_progress_fake(monkeypatch)

    class _FailingPipeline:
        def run(self, document):
            progress.register(document)
            raise RuntimeError("CUDA out of memory")

    image = tmp_path / "0001.jpg"
    image.write_bytes(b"jpg")

    from htrflow_batch.driver import process_page

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        process_page(_FailingPipeline(), image, tmp_path / "outputs")
    assert (progress._tasks, progress._steps, progress._exports) == ({}, {}, {})


class _FakeThread:
    """An htrflow worker thread, as the guard sees it: alive until it isn't."""

    def __init__(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


class _FakeStep:
    """An Inference step: a worker thread plus the StepMetadata htrflow fills
    in from the model (steps.py: ``StepMetadata(str(self), model.metadata)``)."""

    def __init__(self, name="Segmentation", model="Riksarkivet/yolov9-regions-1"):
        self._name = name
        self._thread = _FakeThread()
        self._queue = SimpleNamespace(_thread=_FakeThread())  # BatchedQueue's own
        self.model = object()
        self.metadata = SimpleNamespace(
            description=name, settings={"model_class": "YOLO", "model": model}
        )

    def __str__(self) -> str:
        return self._name


DEAD_SENTENCE = (
    "page 0044: htrflow's Segmentation (model Riksarkivet/yolov9-regions-1) "
    "worker thread died; the page is marked failed and the pipeline is rebuilt"
)


def _image(tmp_path, stem="0044"):
    path = tmp_path / f"{stem}.jpg"
    path.write_bytes(b"jpg")
    return path


def test_process_page_fails_the_page_when_a_step_thread_dies_mid_run(
    tmp_path, monkeypatch, fake_htrflow
):
    """B88 (2026-09-08): htrflow's YOLO step raised inside its daemon thread,
    the thread died and `pipeline.run` blocked forever on a future nobody
    would complete -- the pod held its GPU until the 6 h deadline. The guard
    must turn that deadlock into one failed page."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    monkeypatch.setattr(driver, "THREAD_POLL_SECONDS", 0.01)
    blocked = threading.Event()
    step = _FakeStep()

    class _DyingPipeline:
        steps = [step]

        def run(self, document):
            step._thread.alive = False
            blocked.wait(30)  # htrflow: waiting on the dead thread's queue

    try:
        with pytest.raises(driver.PipelineDead) as excinfo:
            driver.process_page(_DyingPipeline(), _image(tmp_path), tmp_path / "out")
        assert str(excinfo.value) == DEAD_SENTENCE
    finally:
        blocked.set()  # release the daemon helper thread


def test_process_page_checks_the_threads_before_it_starts_the_run(
    tmp_path, fake_htrflow
):
    """A pipeline handed in already dead must fail the page without enqueueing
    it -- putting work on a dead queue is what blocks forever."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    step = _FakeStep()
    step._thread.alive = False
    runs = []

    class _DeadPipeline:
        steps = [step]

        def run(self, document):
            runs.append(document)

    with pytest.raises(driver.PipelineDead, match="worker thread died"):
        driver.process_page(_DeadPipeline(), _image(tmp_path), tmp_path / "out")
    assert runs == []


def test_process_page_runs_normally_while_the_step_threads_are_alive(
    tmp_path, fake_htrflow
):
    """The guard is transparent: a healthy pipeline still returns both
    formats, and the helper thread it runs in does not swallow the outputs."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    out_dir = tmp_path / "outputs"

    class _WritingPipeline:
        steps = [_FakeStep()]

        def run(self, document):
            for fmt in ("alto", "page"):
                (out_dir / fmt).mkdir(parents=True, exist_ok=True)
                (out_dir / fmt / "0044.xml").write_text("<x/>")

    assert set(driver.process_page(_WritingPipeline(), _image(tmp_path), out_dir)) == {
        "alto",
        "page",
    }


def test_process_page_reraises_the_pipelines_own_exception_from_the_helper(
    tmp_path, fake_htrflow
):
    """A step that raises in OUR thread (htrflow's non-threaded steps, and any
    Inference error the future carries back) must reach the caller unchanged,
    not be lost in the helper thread."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    class _RaisingPipeline:
        steps = [_FakeStep()]

        def run(self, document):
            raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        driver.process_page(_RaisingPipeline(), _image(tmp_path), tmp_path / "out")


def test_process_page_watches_the_batched_queues_thread_too(
    tmp_path, monkeypatch, fake_htrflow
):
    """An Inference step runs TWO daemon threads: its own ``_process`` and
    the ``BatchedQueue``'s, which turns single puts into batches. If the
    queue's dies, ``put`` returns a future nobody will ever batch and the run
    hangs exactly the same way -- with ``step._thread`` still alive."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    monkeypatch.setattr(driver, "THREAD_POLL_SECONDS", 0.01)
    blocked = threading.Event()
    step = _FakeStep()

    class _DyingQueuePipeline:
        steps = [step]

        def run(self, document):
            step._queue._thread.alive = False
            blocked.wait(30)

    try:
        with pytest.raises(driver.PipelineDead) as excinfo:
            driver.process_page(
                _DyingQueuePipeline(), _image(tmp_path), tmp_path / "out"
            )
        assert str(excinfo.value) == DEAD_SENTENCE
    finally:
        blocked.set()


def test_release_pipeline_drops_the_models_of_a_dead_pipeline():
    """The helper thread parked in ``run`` holds the STEP, so dropping the
    pipeline reference frees nothing: without this the rebuild would load a
    second full set of weights onto the same GPU."""
    from htrflow_batch import driver

    steps = [_FakeStep(), _FakeStep()]
    pipeline = SimpleNamespace(steps=list(steps))
    driver.release_pipeline(pipeline)
    assert [step.model for step in steps] == [None, None]


def test_release_pipeline_survives_a_step_that_keeps_no_model():
    """Export steps (and an htrflow that renames the attribute) must not turn
    freeing the GPU into the error the page is reported with."""
    from htrflow_batch import driver

    class _NoModel:
        __slots__ = ()

    after = SimpleNamespace(model="weights")
    driver.release_pipeline(SimpleNamespace(steps=[_NoModel(), after]))
    assert after.model is None  # the refusing step must not shield the rest


def _inject_step_building_fake(fake_htrflow) -> list:
    """A fake htrflow whose ``Pipeline.from_config`` builds its steps the way
    the real one does (pipeline.py): one module-level ``init_step`` call per
    YAML step, collected into a list ``from_config`` keeps to itself. Returns
    the steps it actually built, in order."""
    import yaml

    built = []

    class _Step:
        def __init__(self, name):
            self.name = name
            self.model = f"weights-{name}"

    def init_step(name):
        if name == "boom":
            raise NotImplementedError("Model X is not supported")
        step = _Step(name)
        built.append(step)
        return step

    class MockPipeline:
        def __init__(self, steps):
            self.steps = steps

        @staticmethod
        def from_config(path):
            with open(path) as handle:
                config = yaml.safe_load(handle)
            # resolved from the module global on every call, as htrflow's own
            # `from htrflow.pipeline.steps import init_step` name is
            return MockPipeline([module.init_step(s) for s in config["steps"]])

    module = fake_htrflow(
        pipeline={"Pipeline": MockPipeline, "init_step": init_step},
        steps={"Export": _Export},
    )
    return built


def test_build_pipeline_releases_the_steps_it_built_before_a_failure(
    tmp_path, fake_htrflow
):
    """W1: htrflow's Inference.__init__ starts a daemon thread bound to the
    step, so a construction that raises half-way leaves every finished step
    -- and its model weights -- reachable forever. A rebuild that keeps
    failing would take the GPU out with it."""
    from htrflow_batch import driver

    built = _inject_step_building_fake(fake_htrflow)
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: [ok, boom]")

    with pytest.raises(ValueError, match="bad pipeline config"):
        driver.build_pipeline(str(pipeline_yaml))

    assert [step.name for step in built] == ["ok"]
    assert built[0].model is None


def test_build_pipeline_keeps_the_steps_of_a_pipeline_that_built(
    tmp_path, fake_htrflow
):
    """The teardown must reach only a failed construction: a pipeline that
    built is returned with its weights, and htrflow's own ``init_step`` is
    back where it was."""
    from htrflow_batch import driver

    built = _inject_step_building_fake(fake_htrflow)
    from htrflow.pipeline import pipeline as mod  # the fake injected above

    original = mod.init_step
    pipeline_yaml = tmp_path / "pipeline.yaml"
    pipeline_yaml.write_text("steps: [ok, fine]")

    pipeline = driver.build_pipeline(str(pipeline_yaml))

    assert [step.model for step in built] == ["weights-ok", "weights-fine"]
    assert len(pipeline.steps) == 2
    assert mod.init_step is original  # swapped only for the construction


def test_a_page_that_finished_is_not_failed_by_a_late_thread_death(
    tmp_path, monkeypatch, fake_htrflow
):
    """W17: the guard looks at the step threads every second WHILE the run is
    waiting. A thread that dies in the same tick the run completes made it
    fail a page whose outputs were already written -- and the failure path
    then deleted them, so the page was redone on the retry for nothing."""
    _inject_process_fakes(fake_htrflow)
    from htrflow_batch import driver

    monkeypatch.setattr(driver, "THREAD_POLL_SECONDS", 0.01)
    out_dir = tmp_path / "out"
    blocked = threading.Event()

    class _Pipeline:
        steps = ()

        def run(self, document):
            blocked.wait(5)  # held until the guard is inside a check
            for fmt in ("alto", "page"):
                path = out_dir / fmt / "0044.xml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("<x/>")

    looks = []
    step = _FakeStep()

    def dead_step(pipeline):
        looks.append(1)
        if len(looks) == 1:
            return None  # the check before the run: everything alive
        blocked.set()  # the run finishes while this check is still going
        (helper,) = [t for t in threading.enumerate() if t.name == "htrflow-page-0044"]
        helper.join(5)  # ... and is recorded as done
        return step  # only now is the dead thread visible

    monkeypatch.setattr(driver, "_dead_step", dead_step)

    files = driver.process_page(_Pipeline(), _image(tmp_path), out_dir)

    assert sorted(files) == ["alto", "page"]
    assert files["alto"].exists() and files["page"].exists()


class _HtrflowQueue:
    """htrflow's BatchedQueue as it is (batched_queue.py): a daemon thread
    polling ``_in`` every ``patience`` seconds, for ever."""

    def __init__(self, patience=0.01):
        import queue

        self.patience = patience
        self._in, self._out = queue.Queue(), queue.Queue()
        self._thread = threading.Thread(target=self._process, daemon=True)
        self._thread.start()

    def _process(self):
        import queue

        while 1:
            batch = []
            while len(batch) < 1:
                try:
                    batch.append(self._in.get(timeout=self.patience))
                except queue.Empty:
                    continue
            self._out.put(batch)

    def get(self):
        return self._out.get()


class _HtrflowStep:
    """htrflow's Inference as it is (steps.py): a daemon thread blocked on
    the queue, calling the model on each batch, for ever."""

    def __init__(self, model=lambda images: images):
        self.model = model
        self._queue = _HtrflowQueue()
        self._thread = threading.Thread(target=self._process, daemon=True)
        self._thread.start()

    def _process(self):
        while 1:
            batch = self._queue.get()
            self.model([item for item in batch])


@pytest.fixture
def abandoned(monkeypatch):
    """The driver with an empty abandoned list, and the interpreter's own
    thread excepthook in place of pytest's, which reports even SystemExit."""
    from htrflow_batch import driver

    monkeypatch.setattr(driver, "_ABANDONED", [])
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    return driver


def test_releasing_a_pipeline_ends_its_worker_threads(abandoned, capfd):
    """Audit 0923 W-8: every rebuild after a dead worker thread left the old
    pipeline's other threads running for the life of the process -- each
    BatchedQueue polling ten times a second. Released, they end, and end
    quietly: SystemExit is the one exception a thread dies of without a
    traceback in the run log."""
    steps = [_HtrflowStep(), _HtrflowStep()]
    threads = [t for s in steps for t in (s._thread, s._queue._thread)]

    abandoned.release_pipeline(SimpleNamespace(steps=steps))

    assert abandoned.leaked_threads(grace=2.0) == 0
    assert not any(t.is_alive() for t in threads)
    assert "Traceback" not in capfd.readouterr().err


def test_a_worker_stuck_in_its_model_is_counted_as_leaked(abandoned):
    """What cannot be stopped is counted: a model call that never returns
    keeps its thread, and whatever that holds on the GPU, for good."""
    entered, stuck = threading.Event(), threading.Event()
    step = _HtrflowStep(model=lambda images: (entered.set(), stuck.wait(30)))
    step._queue._in.put("page")
    assert entered.wait(5)  # the step's thread is inside the model now
    try:
        abandoned.release_pipeline(SimpleNamespace(steps=[step]))
        assert abandoned.leaked_threads(grace=0.3) == 1
    finally:
        stuck.set()
    assert abandoned.leaked_threads(grace=2.0) == 0


def test_a_page_that_makes_no_progress_is_a_dead_pipeline(
    tmp_path, monkeypatch, fake_htrflow, abandoned
):
    """Audit 0923 W-8: there was no per-page bound, so a hung model held the
    GPU until activeDeadlineSeconds, and every retry hung the same way. Past
    its budget the page fails as PipelineDead -- the pipeline is rebuilt --
    and the run's helper thread, still inside htrflow, is counted."""
    _inject_process_fakes(fake_htrflow)
    monkeypatch.setattr(abandoned, "THREAD_POLL_SECONDS", 0.01)
    hang = threading.Event()

    class _HungPipeline:
        steps: list = []

        def run(self, document):
            hang.wait(30)

    try:
        with pytest.raises(abandoned.PipelineDead, match="no progress for 0.2 s"):
            abandoned.process_page(
                _HungPipeline(), _image(tmp_path), tmp_path / "out", seconds=0.2
            )
        assert abandoned.leaked_threads(grace=0.1) == 1
    finally:
        hang.set()


def _steady_queue_pipeline(batches: int, gap: float):
    """An Inference step's queue as the watchdog sees it: every batch the
    model finishes, its worker takes the next one off ``_out``."""
    import queue

    step = SimpleNamespace(
        _queue=SimpleNamespace(_in=queue.Queue(), _out=queue.Queue())
    )

    class _Pipeline:
        steps = [step]

        def run(self, document):
            for i in range(batches):
                step._queue._out.put(i)
            for _ in range(batches):
                time.sleep(gap)
                step._queue._out.get()

    return _Pipeline()


def test_a_slow_page_that_keeps_making_progress_completes(
    tmp_path, monkeypatch, fake_htrflow, abandoned
):
    """Review I-2: the budget was a total per page, and a broadsheet page of
    1 500 lines on TrOCR legitimately takes 300-1 000 s -- it was failed, and
    its model, not hung at all, went on running beside the rebuilt one. The
    budget is a no-progress window: every batch the model finishes restarts
    it, so a page far longer than the window completes."""
    _inject_process_fakes(fake_htrflow)
    monkeypatch.setattr(abandoned, "THREAD_POLL_SECONDS", 0.01)
    out = tmp_path / "out"
    for fmt in ("alto", "page"):
        (out / fmt).mkdir(parents=True)
        (out / fmt / "0044.xml").write_text("<x/>")
    pipeline = _steady_queue_pipeline(batches=10, gap=0.02)  # twice the window

    files = abandoned.process_page(pipeline, _image(tmp_path), out, seconds=0.1)
    assert set(files) == {"alto", "page"}
    assert abandoned.leaked_threads(grace=0.1) == 0


def test_a_step_that_finishes_is_progress_too(
    tmp_path, monkeypatch, fake_htrflow, abandoned
):
    """Steps with no worker queue (reading order, the Exports) show their
    progress in htrflow's own registry: Pipeline.run records each step."""
    _inject_process_fakes(fake_htrflow)
    progress = _inject_progress_fake(monkeypatch)
    monkeypatch.setattr(abandoned, "THREAD_POLL_SECONDS", 0.01)
    out = tmp_path / "out"
    for fmt in ("alto", "page"):
        (out / fmt).mkdir(parents=True)
        (out / fmt / "0044.xml").write_text("<x/>")

    class _ManySteps:
        steps: list = []

        def run(self, document):
            for i in range(10):  # twice the window in all
                time.sleep(0.02)
                progress._steps.setdefault(document, []).append(f"step {i}")

    files = abandoned.process_page(_ManySteps(), _image(tmp_path), out, seconds=0.1)
    assert set(files) == {"alto", "page"}


class _ZombieExport:
    """An Export as htrflow has it: writes the page's file, then registers
    the path in the module-global progress registry."""

    def __init__(self, dest, progress):
        self.dest, self.progress = dest, progress

    def __str__(self) -> str:
        return "Export"

    def run(self, document):
        self.dest.mkdir(parents=True, exist_ok=True)
        (self.dest / "0044.xml").write_text("<late/>")
        self.progress._exports.setdefault(document, []).append("late")
        return document


def test_a_dead_pipeline_runs_no_further_step(
    tmp_path, monkeypatch, fake_htrflow, abandoned
):
    """Review I-3: the helper of a page past its no-progress window is not
    stopped by stopping the worker threads -- when the slow call returns it
    goes on to the next step, and the Exports the wrapper appends then wrote
    outputs/<fmt>/<stem>.xml for a page already failed and cleaned up, and
    touched htrflow's progress registry beside the live pipeline. A dead
    pipeline refuses every step it has not started, before htrflow's
    Pipeline.run can record it."""
    _inject_process_fakes(fake_htrflow)
    progress = _inject_progress_fake(monkeypatch)
    monkeypatch.setattr(abandoned, "THREAD_POLL_SECONDS", 0.01)
    out = tmp_path / "out"
    stalled = threading.Event()
    returned = threading.Event()

    class _Stall:
        def __str__(self) -> str:
            return "TextRecognition"

        def run(self, document):
            stalled.wait(30)
            return document

    class _HtrflowPipeline:
        """htrflow's Pipeline.run, as it is (pipeline.py)."""

        def __init__(self, steps):
            self.steps = steps

        def run(self, document):
            try:
                for step in self.steps:
                    progress.step(document, step=step)
                    document = step.run(document)
                progress.done(document)
            finally:
                returned.set()

    def step(document, step):
        status = str(step)  # htrflow: update(document, status=str(step)) first
        progress._steps.setdefault(document, []).append(status)

    progress.step = step
    progress.done = lambda document: progress._tasks.setdefault(document, "done")
    pipeline = _HtrflowPipeline(
        [
            _Stall(),
            _ZombieExport(out / "alto", progress),
            _ZombieExport(out / "page", progress),
        ]
    )
    with pytest.raises(abandoned.PipelineDead):
        abandoned.process_page(pipeline, _image(tmp_path), out, seconds=0.1)
    abandoned.release_pipeline(pipeline)
    stalled.set()  # the slow call returns, and the zombie goes on
    assert returned.wait(5)
    assert not (out / "alto").exists() and not (out / "page").exists()
    assert (progress._steps, progress._exports, progress._tasks) == ({}, {}, {})


def test_a_step_missing_the_threads_it_should_have_is_loud(abandoned, caplog):
    """Review M-5: an htrflow that renamed ``_queue``/``_in``/``_out``/
    ``_thread`` made the stop a silent no-op, and its threads were never
    counted. A step that holds a model is htrflow's Inference, and one not
    shaped like it is logged at ERROR and counted as leaked."""
    step = SimpleNamespace(model=object(), _worker=threading.Thread(target=print))
    with caplog.at_level("ERROR"):
        abandoned.release_steps([step])
    assert "cannot stop" in caplog.text
    assert abandoned.leaked_threads(grace=0.0) == 1


def test_a_step_without_a_model_has_no_threads_to_stop(abandoned, caplog):
    """An Export, a reading-order step: nothing to stop, nothing to say."""
    with caplog.at_level("ERROR"):
        abandoned.release_steps([SimpleNamespace(dest="out")])
    assert caplog.text == ""
    assert abandoned.leaked_threads(grace=0.0) == 0
