"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (docs: wrapper)."""

from __future__ import annotations

import gc
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import yaml

from .stream import discard

#: Every format the wrapper appends an Export step for; process_page requires
#: all of them and store.upload_page uploads them page-first.
EXPECTED_FORMATS = ("alto", "page")


def _refused_the_path(Pipeline, e: BaseException) -> bool:
    """Whether ``from_config`` refused the ARGUMENT -- an older htrflow whose
    ``from_config`` takes a parsed dict, handed a path -- rather than a step
    or model constructor deeper in raising a TypeError of its own (W2).

    Told apart by where the TypeError was raised: in ``from_config``'s own
    code object, or below it. The distinction matters because the pinned
    htrflow's ``from_config`` does ``open(path)``, so retrying a constructor's
    TypeError with the dict only raises a second one from ``open(dict)`` --
    and a bare TypeError is not permanent, so a mistyped pipeline setting
    exited 1 and burned every retry instead of failing at once.
    """
    tb = e.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    function = getattr(Pipeline.from_config, "__func__", Pipeline.from_config)
    return tb is not None and tb.tb_frame.f_code is getattr(function, "__code__", None)


@contextmanager
def _tracked_steps(built: list):
    """Record every step htrflow builds, so a failed construction can be torn
    down (W1). ``Pipeline.from_config`` owns the loop that calls ``init_step``
    and keeps its results in a local list, so the only way to reach the steps
    made before the failure is to see them go past: the name is swapped for
    the duration of the construction and put back whatever happens. Only the
    main thread builds pipelines, and an htrflow that does not expose the name
    is simply not tracked."""
    from htrflow.pipeline import pipeline as module  # ty: ignore[unresolved-import]

    original = getattr(module, "init_step", None)
    if original is None:
        yield
        return

    def tracking(step_config):
        # Recorded only once init_step RETURNS, so the step whose own
        # construction raised is not in `built`. That is right for htrflow as
        # it is: Inference.__init__ starts its daemon thread last, after the
        # model is on the GPU, so a step that raised has nothing holding it
        # and the collector takes it and its weights. A step that started a
        # thread before it could fail would need the tracking one level down,
        # inside htrflow.
        step = original(step_config)
        built.append(step)
        return step

    module.init_step = tracking
    try:
        yield
    finally:
        module.init_step = original


def _steps(config) -> list[dict]:
    """The step entries of a parsed pipeline YAML, as htrflow will read them.
    A shape htrflow itself refuses (no ``steps`` list, a step that is not a
    mapping) is left to its own validation, which fails as permanently."""
    steps = config.get("steps") if isinstance(config, dict) else None
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


#: What htrflow's ``Inference.from_config`` takes off a model step's
#: ``settings`` before it calls the model: everything else is merged OVER
#: ``model_settings`` -- ``model_settings | <the rest>`` (htrflow steps.py).
_MODEL_STEP_SETTINGS = ("model", "model_settings", "generation_settings")

#: Where a revision reaches a loader: YOLO and PyLaia take ``revision``, the
#: Hugging Face models (TrOCR, DiT, Donut) ``model_kwargs.revision`` -- the two
#: paths the chart's model-revision policy accepts a pin on.
_PIN_PATHS = (("revision",), ("model_kwargs", "revision"))

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _at(mapping: object, path: tuple[str, ...]) -> object:
    for key in path:
        mapping = mapping.get(key) if isinstance(mapping, dict) else None
    return mapping


def _is_commit(value: object) -> bool:
    return isinstance(value, str) and bool(_COMMIT.match(value))


def _model_kwargs(settings: dict) -> tuple[dict, dict]:
    """A model step's ``model_settings`` as written, and the keyword arguments
    htrflow will actually hand the model -- the same merge, reproduced."""
    written = settings.get("model_settings")
    written = written if isinstance(written, dict) else {}
    rest = {k: v for k, v in settings.items() if k not in _MODEL_STEP_SETTINGS}
    return written, written | rest


def _check_pins(index: int, step: dict) -> None:
    """3058: a pin under ``model_settings`` -- the one the model-revision
    policy and ``validate`` read -- must be the revision the model gets. A
    key beside it wins the merge (``revision: null`` for YOLO, an empty
    ``model_kwargs`` for TrOCR) and loads the repo's head, which can be
    pickled code. Both of those refuse the shape already; this is the layer
    that holds when a pipeline reached the pod past them. A step pinned
    nowhere is not this rule's to judge: whether that is allowed is the
    chart's ``requireModelRevision``, which admission enforces."""
    settings = step.get("settings")
    if not isinstance(settings, dict) or "model" not in settings:
        return
    written, used = _model_kwargs(settings)
    for path in _PIN_PATHS:
        pin, effective = _at(written, path), _at(used, path)
        if _is_commit(pin) and not _is_commit(effective):
            where = ".".join(path)
            raise ValueError(
                f"step {index} ({step.get('step', '?')}): model "
                f"{written.get('model', '?')} is not pinned to a commit — "
                f"model_settings.{where} is {pin}, but the {path[0]} key beside "
                f"model_settings overrides it and htrflow would load revision "
                f"{effective!r}; move every model setting under model_settings"
            )


def _check_steps(config) -> None:
    """The rules a pipeline file breaks by its text alone, checked on the
    parsed YAML BEFORE ``from_config`` builds anything (3098). Built, every
    model step has put its weights on the GPU -- and the warm-up, which never
    appended Exports, used to pass a pipeline every batch index then loaded
    in full only to exit 13 on."""
    for index, step in enumerate(_steps(config), 1):
        # htrflow resolves a step by its lower-cased name (steps.STEPS)
        if str(step.get("step", "")).lower() == "export":
            raise ValueError(
                "pipeline YAML must not contain Export steps; "
                "the wrapper appends them (docs: wrapper)"
            )
        _check_pins(index, step)


def build_pipeline(pipeline_path: str):
    """The pipeline htrflow builds from the YAML, as both callers need it: the
    driver, which then appends the Export steps, and warm-up, where the
    construction IS the model download. One place translates htrflow's
    config mistakes."""
    # htrflow ships in the runtime base image, not in this workspace's lock,
    # so it is unresolvable to the type checker by design.
    from htrflow.pipeline.pipeline import Pipeline  # ty: ignore[unresolved-import]

    # Validate the pipeline file up front: missing/unreadable/malformed YAML
    # is a config mistake — surface it as ValueError so main.py classifies it
    # PERMANENT (exit 13). from_config itself also instantiates models (HF
    # downloads), so an OSError raised *there* may be a flaky network and
    # must propagate untouched (TRANSIENT, exit 1).
    try:
        with open(pipeline_path) as f:
            config = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        raise ValueError(f"bad pipeline config: {e}") from e
    _check_steps(config)

    built: list = []
    try:
        with _tracked_steps(built):
            try:
                return Pipeline.from_config(str(pipeline_path))
            except TypeError as e:
                if not _refused_the_path(Pipeline, e):
                    raise
                # older htrflow builds: from_config takes a parsed config dict
                return Pipeline.from_config(config)
    except BaseException as e:
        # W1 (2026-09-14, audit): a construction that raises part-way has
        # already built every step before the failing one, and each Inference
        # among them started a daemon thread bound to itself -- so nothing
        # collects them and their weights sit on the GPU for the life of the
        # process. A rebuild that keeps failing (main.py) would repeat that
        # per page until the GPU is out of memory, so the partial pipeline is
        # torn down here, where it is still reachable.
        release_steps(built)
        # htrflow: KeyError from STEPS[name] for an unknown step,
        # NotImplementedError from get_model_by_name for an unknown model
        # class, and TypeError from a step or model constructor, which htrflow
        # hands the YAML's `settings:` as keyword arguments (W2). Config
        # mistakes -> PERMANENT, like malformed YAML above.
        if isinstance(e, (KeyError, NotImplementedError, TypeError)):
            raise ValueError(f"bad pipeline config: unknown step or model: {e}") from e
        raise


def load_pipeline(pipeline_path: str, out_dir: Path):
    from htrflow.pipeline.pipeline import Pipeline  # ty: ignore[unresolved-import]
    from htrflow.pipeline.steps import Export  # ty: ignore[unresolved-import]

    pipeline = build_pipeline(pipeline_path)  # refuses Export steps (3098)
    try:
        exports = [Export(str(out_dir / fmt), fmt) for fmt in EXPECTED_FORMATS]
        # rebuild so Pipeline.__init__ wires the new steps the same way as the
        # originals (older htrflow sets parent_pipeline there; append leaves the
        # Export orphaned and its metadata None)
        return Pipeline(list(pipeline.steps) + exports)
    except BaseException:
        release_pipeline(pipeline)  # W1: the same leak, one construction later
        raise


def release_documents() -> None:
    """Empty htrflow's ``progress`` registries: module-global dicts keyed by
    ``Document``, filled by Pipeline.run and Export and popped by nothing
    (the rich progress singleton keeps a task per entry too). htrflow's CLI
    runs one process per volume and never notices; the wrapper runs one
    long-lived Pipeline over a whole volume, so every page's Region tree
    would stay reachable until the process exits (~0.5 GB at 10 000 pages,
    audit X2).

    Emptying them wholesale is right here and only here: this process has one
    caller of htrflow and one page in flight at a time, so when a page is done
    nothing in them is still wanted -- and naming the Document objects instead
    would miss the ones we never see, since every ProcessImages-type step
    returns a new one. Best-effort throughout: an htrflow without those
    registries, or a task the singleton has already dropped, must not cost a
    page. A helper thread still parked in a dead pipeline's ``run`` (B88) may
    call ``progress.update`` for a document popped here; that is benign --
    htrflow re-registers a document it does not find."""
    try:
        from htrflow import progress  # ty: ignore[unresolved-import]
    except Exception:
        return
    tasks = getattr(progress, "_tasks", {})
    for document in list(tasks):
        try:
            progress._progress.remove_task(tasks.pop(document))
        except Exception:
            pass  # one page's bookkeeping must not cost the next one's
    for name in ("_exports", "_steps"):
        try:
            getattr(progress, name, {}).clear()
        except Exception:
            pass


class PipelineDead(RuntimeError):
    """An htrflow step's worker thread is gone, so this pipeline can never
    finish another page. A page failure, not a volume failure: main rebuilds
    the pipeline and the run goes on (docs: failure-handling)."""


#: How often the guard looks at the step threads while a page is running.
#: A page takes ~13 s, so a second costs nothing and bounds the stall.
THREAD_POLL_SECONDS = 1.0


def _dead_step(pipeline):
    """The first step whose worker thread has died, if any.

    An Inference step runs TWO daemon threads: its own ``_process`` and the
    ``BatchedQueue``'s, which collects single puts into batches
    (batched_queue.py). Either one dying hangs the run the same way -- with
    the queue's gone, ``put`` returns a future nobody will ever batch -- so
    both are watched. Every other kind of step has neither, and a step
    without them is never dead.
    """
    for step in getattr(pipeline, "steps", ()):
        queue = getattr(step, "_queue", None)
        for thread in (getattr(step, "_thread", None), getattr(queue, "_thread", None)):
            if thread is not None and not thread.is_alive():
                return step
    return None


def _dead(step, stem: str) -> PipelineDead:
    """One sentence naming the step and the model it was running: htrflow
    keeps the model id in the step's StepMetadata.settings."""
    settings = getattr(getattr(step, "metadata", None), "settings", None) or {}
    model = settings.get("model") or settings.get("model_class") or "unknown"
    return PipelineDead(
        f"page {stem}: htrflow's {step} (model {model}) worker thread died; "
        "the page is marked failed and the pipeline is rebuilt"
    )


def release_pipeline(pipeline) -> None:
    """Drop a dead pipeline's model weights, before its replacement loads its
    own onto the same GPU.

    Dropping the pipeline reference frees nothing: the helper thread parked
    in ``run`` holds the step (its frame does), and the step holds the model
    -- so a recurring model bug would load one more full set of weights per
    rebuild until the GPU is out of memory. Nothing will ever run this step
    again, so the models go now and the parked thread keeps only itself and
    that page's Document.
    """
    release_steps(getattr(pipeline, "steps", ()))


def release_steps(steps) -> None:
    """The same for a bare list of steps: what a pipeline that never finished
    being constructed leaves behind (W1)."""
    for step in steps:
        try:
            step.model = None
        except Exception:
            pass  # freeing the GPU must never replace the page's own error
    gc.collect()  # the step frees the model only once nothing refers to it
    try:
        import torch  # ty: ignore[unresolved-import]

        torch.cuda.empty_cache()  # give the freed blocks back to the driver
    except Exception:
        pass  # no torch, or a CPU-only run: nothing cached to give back


def _run_guarded(pipeline, document, stem: str) -> None:
    """``pipeline.run`` with a watch on the steps' worker threads.

    An Inference step hands its batch to a daemon thread and waits on a
    Future (htrflow steps.py). An exception in that thread -- 2026-09-08: a
    YOLO detection without a polygon -- kills the thread, and ``run`` then
    waits forever for a future nobody will complete: the pod stood still
    with its GPU reserved until the 6 h deadline and was retried onto the
    same page three times. So the run goes into a helper thread and the
    liveness check runs before it starts and every second while it waits.
    There is no check afterwards: a worker thread dies before it can
    complete the batch's futures, so a run that HAS returned got its results
    -- failing that page would throw away complete outputs, and the next
    page's check before the run catches the dead pipeline anyway.

    The helper is a daemon and is never joined: when a run IS stuck it stays
    parked on the dead queue for the life of the process, holding that one
    page's document. Nothing waits on it, and the pod's activeDeadlineSeconds
    is still the backstop for the process as a whole.
    """

    failure: list[BaseException] = []
    done = threading.Event()

    def check() -> None:
        step = _dead_step(pipeline)
        # W17: a thread that dies in the same tick the run completes must not
        # fail a page that is finished -- its outputs are written, and the
        # failure path below deletes them, so the page would be redone on the
        # retry for nothing. The dead pipeline is caught by the next page's
        # check before its run, which is the same guarantee as the one this
        # function's docstring makes for a run that has already returned.
        if step is not None and not done.is_set():
            raise _dead(step, stem)

    check()  # never enqueue onto a dead queue: that is what blocks forever

    def run() -> None:
        try:
            pipeline.run(document)
        except BaseException as e:  # re-raised below, in the page's own thread
            failure.append(e)
        finally:
            done.set()

    threading.Thread(target=run, name=f"htrflow-page-{stem}", daemon=True).start()
    while not done.wait(THREAD_POLL_SECONDS):
        check()
    if failure:
        raise failure[0]


def _outputs(out_dir: Path, stem: str) -> dict[str, Path]:
    """The files this page's Export steps actually wrote, by format."""
    found: dict[str, Path] = {}
    for fmt in EXPECTED_FORMATS:
        matches = (
            sorted((out_dir / fmt).glob(f"**/{stem}*.xml"))
            if (out_dir / fmt).exists()
            else []
        )
        if matches:
            found[fmt] = matches[0]
    return found


def process_page(pipeline, image_path: Path, out_dir: Path) -> dict[str, Path]:
    from htrflow.pipeline.steps import auto_import  # ty: ignore[unresolved-import]

    stem = image_path.stem
    try:
        for document in auto_import([str(image_path)]):
            _run_guarded(pipeline, document, stem)
        files = _outputs(out_dir, stem)
        missing = [fmt for fmt in EXPECTED_FORMATS if fmt not in files]
        if missing:
            # W2: a half-written page must fail here, not be uploaded with one
            # format and later counted as done.
            raise RuntimeError(f"page {stem}: no {', '.join(missing)} output written")
        return files
    except BaseException:
        # X2: consume's rolling delete reaches only the files we RETURN, so a
        # page that fails after one Export landed must take that file with it
        # -- the memory-backed workdir would keep it for the whole volume.
        try:  # the cleanup must never replace the exception being raised
            for path in _outputs(out_dir, stem).values():
                discard(path)
        except OSError:
            pass
        raise
    finally:
        # A failed page registered documents too, so this belongs on every
        # path out, not only the successful one.
        release_documents()


def htrflow_version() -> str:
    try:
        from importlib.metadata import version

        return version("htrflow")
    except Exception:
        return "unknown"
