"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (docs: wrapper)."""

from __future__ import annotations

from pathlib import Path

import yaml

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


def process_page(pipeline, image_path: Path, out_dir: Path) -> dict[str, Path]:
    from htrflow.pipeline.steps import auto_import  # ty: ignore[unresolved-import]

    for document in auto_import([str(image_path)]):
        pipeline.run(document)
    stem = image_path.stem
    files: dict[str, Path] = {}
    missing: list[str] = []
    for fmt in EXPECTED_FORMATS:
        matches = (
            sorted((out_dir / fmt).glob(f"**/{stem}*.xml"))
            if (out_dir / fmt).exists()
            else []
        )
        if matches:
            files[fmt] = matches[0]
        else:
            missing.append(fmt)
    if missing:
        # W2: a half-written page must fail here, not be uploaded with one
        # format and later counted as done.
        raise RuntimeError(f"page {stem}: no {', '.join(missing)} output written")
    return files


def htrflow_version() -> str:
    try:
        from importlib.metadata import version

        return version("htrflow")
    except Exception:
        return "unknown"
