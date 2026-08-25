"""Warm-up entrypoint: fill ``HF_HOME`` for one pipeline, then exit.

Instantiating the pipeline IS the download — htrflow builds every step's
model at construction (``Pipeline.from_config``), so exactly the files a batch
Job will load land in the cache, with no second parser of the pipeline YAML.
The Job spec that runs this (``build_warmup_job``) is the only pod that mounts
the cache read-write and the only one the NetworkPolicy lets reach HF Hub;
batch Jobs then run ``HF_HUB_OFFLINE=1`` against the same directory.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Callable, Mapping, Optional

from .main import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, prepare_writable_dirs

log = logging.getLogger("htrflow_batch.warmup")

__all__ = ["EXIT_OK", "EXIT_PERMANENT", "EXIT_TRANSIENT", "main"]


def _load(pipeline_path: str) -> None:
    from htrflow.pipeline.pipeline import Pipeline  # ty: ignore[unresolved-import]

    Pipeline.from_config(pipeline_path)


def main(
    env: Optional[Mapping[str, str]] = None,
    load: Callable[[str], object] = _load,
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    env = dict(env if env is not None else os.environ)
    if env.get("HF_HUB_OFFLINE", "") not in ("", "0", "false"):
        # Offline warm-up downloads nothing; "succeeding" would open the gate
        # for a pipeline whose cache is still empty.
        log.error("HF_HUB_OFFLINE is set: a warm-up must be able to reach HF Hub")
        return EXIT_PERMANENT
    pipeline_path = env.get("PIPELINE_PATH", "")
    if not pipeline_path or not Path(pipeline_path).is_file():
        log.error("PIPELINE_PATH missing or not a file: %r", pipeline_path)
        return EXIT_PERMANENT
    prepare_writable_dirs(env)
    if env.get("HF_HOME"):
        Path(env["HF_HOME"]).mkdir(parents=True, exist_ok=True)
    try:
        load(pipeline_path)
    except Exception as e:
        # Network, disk-full, HF Hub 5xx: retryable — the Job's backoffLimit
        # and the reconciler's delete-and-recreate handle it.
        log.error("warm-up failed: %s\n%s", e, traceback.format_exc())
        return EXIT_TRANSIENT
    log.info(
        "warm-up complete: models for %s cached in %s",
        pipeline_path,
        env.get("HF_HOME"),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
