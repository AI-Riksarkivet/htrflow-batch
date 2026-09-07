"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (docs: wrapper)."""

from __future__ import annotations

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
            pipeline.run(document)
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
        for path in _outputs(out_dir, stem).values():
            discard(path)
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
