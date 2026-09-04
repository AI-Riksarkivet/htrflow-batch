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

import yaml
from huggingface_hub.errors import (
    LocalEntryNotFoundError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

from .main import EXIT_OK, EXIT_PERMANENT, EXIT_TRANSIENT, terminate

log = logging.getLogger("htrflow_batch.warmup")

#: Errors a pipeline that is wrong, not unlucky, reaches ``main`` with
#: (docs: wrapper, "Warm-up"). ``driver.build_pipeline`` already translates
#: htrflow's own KeyError/NotImplementedError into ValueError; the two stay
#: listed for a ``load`` callable that does not go through the driver.
#: ``RepositoryNotFoundError``/``RevisionNotFoundError`` (and, by
#: inheritance, ``GatedRepoError``): a bad model id or revision in the
#: pipeline YAML, not a network hiccup — MROs verified in the image's
#: huggingface_hub 0.36.2 (docs: how-it-works/failure-handling.md).
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,  # incl. pydantic ValidationError; driver's "bad pipeline config"
    yaml.YAMLError,
    KeyError,  # unknown step name: htrflow STEPS[step.lower()]
    NotImplementedError,  # unknown model class: htrflow get_model_by_name
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

#: ``LocalEntryNotFoundError`` subclasses ``ValueError`` by MRO (0.36.2), but
#: it means the model is not in the cache yet -- a network miss, not a bad
#: pipeline -- so it must not fall into ``PERMANENT_ERRORS`` by inheritance.
TRANSIENT_FIRST: tuple[type[BaseException], ...] = (LocalEntryNotFoundError,)

__all__ = ["EXIT_OK", "EXIT_PERMANENT", "EXIT_TRANSIENT", "main"]


def _load(pipeline_path: str) -> None:
    """Building the pipeline is the download — the same construction, and the
    same error translation, a batch Job runs (driver.build_pipeline)."""
    from .driver import build_pipeline  # htrflow imports stay function-local

    build_pipeline(pipeline_path)


def _fail(env: Mapping[str, str], msg: str) -> int:
    """Both early-return guards below: a mis-wired deployment (offline warm-up,
    an unreadable ``PIPELINE_PATH``), not a campaign author's mistake, but
    still worth the same termination message the try/except writes -- else
    the campaign card shows "warm-up failed" with no reason at all."""
    log.error(msg)
    terminate(env, {"stage": "warmup", "permanent": True, "error": msg})
    return EXIT_PERMANENT


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
        return _fail(
            env, "HF_HUB_OFFLINE is set: a warm-up must be able to reach HF Hub"
        )
    pipeline_path = env.get("PIPELINE_PATH", "")
    if not pipeline_path or not Path(pipeline_path).is_file():
        return _fail(env, f"PIPELINE_PATH missing or not a file: {pipeline_path!r}")
    try:
        load(pipeline_path)
    except Exception as e:
        # W12: malformed YAML, a config that fails pydantic validation, an
        # unknown step/model class, or a bad HF repo id/revision — retrying
        # cannot help; exit 13 so the warm-up Job's own backoffLimit stops
        # retrying it. Everything else (network, disk-full, HF Hub 5xx, a
        # model merely missing from a not-yet-warm cache) is retryable.
        permanent = isinstance(e, PERMANENT_ERRORS) and not isinstance(
            e, TRANSIENT_FIRST
        )
        log.error(
            "warm-up failed%s: %s",
            " (bad pipeline config)" if permanent else "",
            e if permanent else f"{e}\n{traceback.format_exc()}",
        )
        # Task 28: warm-up termination message. There is no warm-up log (the
        # Job mounts no S3 secret), so this is the only place the bad model
        # id or unknown step reaches the campaign card.
        terminate(env, {"stage": "warmup", "permanent": permanent, "error": str(e)})
        return EXIT_PERMANENT if permanent else EXIT_TRANSIENT
    log.info(
        "warm-up complete: models for %s cached in %s",
        pipeline_path,
        env.get("HF_HOME"),
    )
    _write_marker(env)
    return EXIT_OK


def _write_marker(env: Mapping[str, str]) -> None:
    """Drop ``<data>/warmup/<pipeline_id>.done`` so a batch pod's init
    container can gate on it (docs: wrapper). ``<data>`` is ``HF_HOME``'s
    parent (``/data/hf`` -> ``/data``) so tests can redirect it via env.
    Best-effort: a missing PIPELINE_ID or unwritable dir must not turn a
    successful warm-up into a failure.
    """
    pipeline_id = env.get("PIPELINE_ID", "")
    hf_home = env.get("HF_HOME", "")
    if not pipeline_id or not hf_home:
        return
    try:
        marker_dir = Path(hf_home).parent / "warmup"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"{pipeline_id}.done").touch()
    except OSError as e:
        log.warning("could not write warm-up marker: %r", e)


if __name__ == "__main__":
    sys.exit(main())
