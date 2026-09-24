#!/usr/bin/env python3
"""Generate the wrapper's contract fixture for the frontend.

Two things the wrapper writes are read by the campaign page with no API in
between to hold them still: the volume's `manifest.json` (the run viewer
parses it with `runManifestSchema`), and the termination message a failed
pod leaves (`reasons.ts` turns its `error` into a sentence -- the verify
messages by their counts and page lists, a stopped pod by its bare
`SIGTERM`). Both used to be hand copies on the frontend side, which a
changed format left green (2026-09-23 test audit).

This writes what the wrapper's own functions produce -- `publish.run_manifest`
for a small run with every page outcome, and `_verify`'s failures and the
SIGTERM reason passed through `terminate`, the 3500-character clip included --
to `frontend/src/lib/fixtures/wrapper-contract.json`, where the vitests read
it. A pytest re-runs this and fails if the committed file is not what it
prints.

Regenerate with `make wrapper-contract`.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from htrflow_batch import main as wrapper
from htrflow_batch import publish
from htrflow_batch.config import Config
from htrflow_batch.iiif import PageRef
from htrflow_batch.stream import PageOutcome, StreamStats

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "frontend" / "src" / "lib" / "fixtures" / "wrapper-contract.json"

#: What the manifest reports as the htrflow version: the real one is read
#: from the installed htrflow, which the image has and this venv does not.
HTRFLOW_VERSION = "0.0.0-contract"
PIPELINE = "steps:\n- step: Segmentation\n- step: Export\n"
#: A pipeline whose QualityPrediction step names a Hub model and revision --
#: what `_manifest_scored` reads back out through `quality.qp_model`.
PIPELINE_QP = (
    "steps:\n"
    "- step: Segmentation\n"
    "- step: QualityPrediction\n"
    "  settings:\n"
    "    model_settings:\n"
    "      model: org/qp-model\n"
    "      revision: " + "a" * 40 + "\n"
    "- step: Export\n"
)


def _pages(n: int) -> list[PageRef]:
    return [
        PageRef(
            index=i,
            name=f"{i:04d}",
            image_url=f"https://iiif.example.org/vol0/p{i}/full/2500,/0/default.jpg",
            canvas={"id": f"https://iiif.example.org/vol0/canvas/{i}"},
        )
        for i in range(1, n + 1)
    ]


def _manifest(work: Path) -> dict:
    """manifest.json of a four-page run: two pages done, one failed with its
    reason, one skipped by resume -- every outcome the viewer draws."""
    (work / "pipeline.yaml").write_text(PIPELINE)
    cfg = Config.from_env(
        {
            "VOLUME_REF": "vol0",
            "IIIF_MANIFEST_URL": "https://iiif.example.org/vol0/manifest",
            "PIPELINE_PATH": str(work / "pipeline.yaml"),
            "PIPELINE_ID": "demo-v1",
            "S3_BUCKET": "htr-results",
            "PUBLIC_RESULTS_BASE": "https://results.example.org/htr-test",
            "WORKDIR_PATH": str(work / "work"),
            "IMAGE_DIGEST": "sha256:" + "0" * 64,
        }
    )
    stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok", seconds=4.21),
            "0002": PageOutcome(
                status="failed",
                seconds=1.5,
                error="page 0002: htrflow's Segmentation worker thread died",
            ),
            "0003": PageOutcome(status="skipped"),
            "0004": PageOutcome(status="ok", seconds=3.9),
        },
        stall_seconds=0.4,
    )
    with mock.patch.object(publish, "_htrflow_version", lambda: HTRFLOW_VERSION):
        return publish.run_manifest(
            cfg,
            _pages(4),
            stats,
            cfg.manifest_url,
            PIPELINE,
            12.5,
            123456,
        )


def _manifest_scored(work: Path) -> dict:
    """The same four-page run, but with QualityPrediction scores on three of
    its pages -- the `quality` block Task 7's pages table sorts by."""
    (work / "pipeline-qp.yaml").write_text(PIPELINE_QP)
    cfg = Config.from_env(
        {
            "VOLUME_REF": "vol0",
            "IIIF_MANIFEST_URL": "https://iiif.example.org/vol0/manifest",
            "PIPELINE_PATH": str(work / "pipeline-qp.yaml"),
            "PIPELINE_ID": "demo-v1",
            "S3_BUCKET": "htr-results",
            "PUBLIC_RESULTS_BASE": "https://results.example.org/htr-test",
            "WORKDIR_PATH": str(work / "work-qp"),
            "IMAGE_DIGEST": "sha256:" + "0" * 64,
        }
    )
    stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok", seconds=4.21),
            "0002": PageOutcome(
                status="failed",
                seconds=1.5,
                error="page 0002: htrflow's Segmentation worker thread died",
            ),
            "0003": PageOutcome(status="skipped"),
            "0004": PageOutcome(status="ok", seconds=3.9),
        },
        stall_seconds=0.4,
    )
    with mock.patch.object(publish, "_htrflow_version", lambda: HTRFLOW_VERSION):
        return publish.run_manifest(
            cfg,
            _pages(4),
            stats,
            cfg.manifest_url,
            PIPELINE_QP,
            12.5,
            123456,
            quality={"0001": 0.9123, "0003": 0.41, "0004": 0.7},
            canvases=["0001", "0003", "0004"],
        )


class _Uploaded:
    """The bucket as `_verify` asks it: which pages have their outputs."""

    def __init__(self, names: set[str]) -> None:
        self.names = names

    def uploaded_pages(self) -> set[str]:
        return set(self.names)


def _termination(work: Path, reason_of: Callable[[dict], None]) -> dict:
    """What the wrapper leaves in the termination log for a run that ends
    the way ``reason_of`` makes it end."""
    log = work / "termination-log"
    env = {"TERMINATION_LOG_PATH": str(log)}
    reason_of(env)
    return json.loads(log.read_text())


def _verify_fails(
    uploaded: set[str], pages: list[PageRef], stats: StreamStats
) -> Callable[[dict], None]:
    """A run whose verify raises, handed to the same `_transient` exit the
    real run takes -- `terminate` redacts and clips the message there."""

    def run(env: dict) -> None:
        state = wrapper.RunState()
        try:
            wrapper._verify(_Uploaded(uploaded), pages, stats, state)
        except RuntimeError as e:
            wrapper._transient(env, state, threading.Event(), e)
        else:
            raise SystemExit("verify passed where the fixture needs it to fail")

    return run


def _failed(names: list[str]) -> StreamStats:
    return StreamStats(
        results={
            n: PageOutcome(
                status="failed", seconds=2.0, error="CUDA error: out of memory"
            )
            for n in names
        }
    )


def _terminations(work: Path) -> dict:
    few, many = _pages(5), _pages(600)
    return {
        # two pages never landed and one failed with a reason
        "verifyMissing": _termination(
            work,
            _verify_fails(
                {"0001", "0004"},
                few,
                StreamStats(
                    results={
                        **_failed(["0005"]).results,
                        "0001": PageOutcome(status="ok", seconds=1.0),
                        "0004": PageOutcome(status="ok", seconds=1.0),
                    }
                ),
            ),
        ),
        # so many missing that the page list runs past the 3500-char clip
        "verifyMissingClipped": _termination(
            work, _verify_fails(set(), many, StreamStats())
        ),
        # every page this attempt processed failed: a model, not a volume
        "verifyAllFailed": _termination(
            work, _verify_fails(set(), few[:3], _failed([p.name for p in few[:3]]))
        ),
        "verifyAllFailedClipped": _termination(
            work,
            _verify_fails(set(), many[:400], _failed([p.name for p in many[:400]])),
        ),
        "sigterm": _termination(
            work,
            # a drain, a pause or the pod's deadline, mid-stream
            lambda env: wrapper.terminate(env, wrapper.sigterm_reason("stream")),
        ),
    }


def build() -> dict:
    logging.disable(logging.CRITICAL)  # _transient logs each failure
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            return {
                "manifest": _manifest(work),
                "manifestScored": _manifest_scored(work),
                "terminations": _terminations(work),
            }
    finally:
        logging.disable(logging.NOTSET)


def main() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
