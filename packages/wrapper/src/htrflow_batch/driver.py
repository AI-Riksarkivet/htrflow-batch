"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (docs: wrapper)."""

from __future__ import annotations

import gc
import threading
from pathlib import Path

import yaml

from .stream import discard

#: Every format the wrapper appends an Export step for; process_page requires
#: all of them and store.upload_page uploads them page-first.
EXPECTED_FORMATS = ("alto", "page")


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

    try:
        try:
            return Pipeline.from_config(str(pipeline_path))
        except TypeError:
            # older htrflow builds: from_config takes a parsed config dict
            return Pipeline.from_config(config)
    except (KeyError, NotImplementedError) as e:
        # htrflow: KeyError from STEPS[name] for an unknown step, and
        # NotImplementedError from get_model_by_name for an unknown model
        # class. Config mistakes -> PERMANENT, like malformed YAML above.
        raise ValueError(f"bad pipeline config: unknown step or model: {e}") from e


def load_pipeline(pipeline_path: str, out_dir: Path):
    from htrflow.pipeline.pipeline import Pipeline  # ty: ignore[unresolved-import]
    from htrflow.pipeline.steps import Export  # ty: ignore[unresolved-import]

    pipeline = build_pipeline(pipeline_path)
    for step in pipeline.steps:
        if isinstance(step, Export):
            raise ValueError(
                "pipeline YAML must not contain Export steps; "
                "the wrapper appends them (docs: wrapper)"
            )
    exports = [Export(str(out_dir / fmt), fmt) for fmt in EXPECTED_FORMATS]
    # rebuild so Pipeline.__init__ wires the new steps the same way as the
    # originals (older htrflow sets parent_pipeline there; append leaves the
    # Export orphaned and its metadata None)
    return Pipeline(list(pipeline.steps) + exports)


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
    page."""
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
    try:
        for step in getattr(pipeline, "steps", ()):
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
    liveness check runs before it starts, every second while it waits, and
    once more after it returns.

    The helper is a daemon and is never joined: when a run IS stuck it stays
    parked on the dead queue for the life of the process, holding that one
    page's document. Nothing waits on it, and the pod's activeDeadlineSeconds
    is still the backstop for the process as a whole.
    """

    def check() -> None:
        step = _dead_step(pipeline)
        if step is not None:
            raise _dead(step, stem)

    check()  # never enqueue onto a dead queue: that is what blocks forever
    failure: list[BaseException] = []
    done = threading.Event()

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
    check()  # a thread that died as the page finished must not take the next one


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
