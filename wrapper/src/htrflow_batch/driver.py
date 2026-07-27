"""htrflow integration. ALL htrflow imports live inside functions so the
wrapper package imports cleanly on hosts without torch (DESIGN.md constraint)."""
from __future__ import annotations

from pathlib import Path


def load_pipeline(pipeline_path: str, out_dir: Path):
    from htrflow.pipeline.pipeline import Pipeline
    from htrflow.pipeline.steps import Export

    try:
        pipeline = Pipeline.from_config(str(pipeline_path))
    except (TypeError, KeyError):
        # older htrflow builds: from_config takes a parsed config dict, not a path
        import yaml
        with open(pipeline_path) as f:
            pipeline = Pipeline.from_config(yaml.safe_load(f))
    for step in pipeline.steps:
        if isinstance(step, Export):
            raise ValueError(
                "pipeline YAML must not contain Export steps; "
                "the wrapper appends them (DESIGN.md §5.7)")
    exports = [Export(str(out_dir / "alto"), "alto"),
               Export(str(out_dir / "page"), "page")]
    # rebuild so Pipeline.__init__ wires the new steps the same way as the
    # originals (older htrflow sets parent_pipeline there; append leaves the
    # Export orphaned and its metadata None)
    pipeline = Pipeline(list(pipeline.steps) + exports)
    return pipeline


def process_page(pipeline, image_path: Path, out_dir: Path) -> dict[str, Path]:
    from htrflow.pipeline.steps import auto_import

    for document in auto_import([str(image_path)]):
        pipeline.run(document)
    stem = image_path.stem
    files: dict[str, Path] = {}
    for fmt in ("alto", "page"):
        matches = sorted((out_dir / fmt).glob(f"**/{stem}*.xml")) \
            if (out_dir / fmt).exists() else []
        if matches:
            files[fmt] = matches[0]
    if not files:
        raise RuntimeError(f"no outputs written for page {stem}")
    return files


def htrflow_version() -> str:
    try:
        from importlib.metadata import version
        return version("htrflow")
    except Exception:
        return "unknown"
