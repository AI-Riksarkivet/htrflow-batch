#!/usr/bin/env python3
"""Generate the read API's contract fixture for the frontend's schemas.

The API is `packages/web` and the page that parses it is `frontend/`, and
nothing tied the two together: a field renamed on one side was found by
whoever next opened a campaign page (2026-09-14 audit). This writes one
document of real projection output -- the same functions the routes call --
to `frontend/src/lib/fixtures/api-contract.json`, where a vitest parses every
row with `jobSummarySchema`/`jobDetailSchema`. A pytest re-runs this and
fails if the committed file is not what it prints, so the fixture cannot go
stale either.

Regenerate with `make api-contract`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "web" / "src"))

from htrflow_web import progress, projection  # noqa: E402

FIXTURE = ROOT / "frontend" / "src" / "lib" / "fixtures" / "api-contract.json"

CFG = SimpleNamespace(
    public_results_base="https://results.example.org",
    internal_results_base="http://rustfs.htr-batch.svc:9000/htr-results",
)

_LABELS = {
    "app": "htrflow-batch",
    "htrflow.riksarkivet.se/managed-by": "converter",
    "htrflow.riksarkivet.se/campaign": "kyrk",
    "htrflow.riksarkivet.se/pipeline": "demo-v1",
}

LIVE_JOB = {
    "metadata": {
        "name": "kyrk",
        "namespace": "htr-test",
        "creationTimestamp": "2026-09-14T07:00:00Z",
        "labels": _LABELS,
    },
    "spec": {"completions": 3, "suspend": False},
    "status": {
        "active": 1,
        "startTime": "2026-09-14T07:01:00Z",
        "completedIndexes": "0",
        "failedIndexes": "2",
        "conditions": [],
    },
}

CAMPAIGN_CM = {
    "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
    "data": {
        "volumes.txt": (
            "vol0\thttps://iiif.example.org/vol0/manifest\n"
            "vol1\timages:https://img.example.org/a.jpg\n"
            "vol2\thttps://iiif.example.org/vol2/manifest\n"
        )
    },
}

PIPELINE_CM = {
    "metadata": {"name": "htr-pipeline-demo-v1", "namespace": "htr-test"},
    "data": {"pipeline.yaml": "steps:\n- step: Segmentation\n- step: Export\n"},
}

FAILED_POD = {
    "metadata": {
        "name": "kyrk-2-abcde",
        "creationTimestamp": "2026-09-14T07:02:00Z",
        "labels": {"batch.kubernetes.io/job-completion-index": "2"},
    },
    "status": {
        "containerStatuses": [
            {
                "name": "wrapper",
                "state": {
                    "terminated": {
                        "exitCode": 1,
                        "message": json.dumps(
                            {
                                "stage": "load",
                                "permanent": True,
                                "error": "manifest 404",
                            }
                        ),
                    }
                },
            }
        ]
    },
}

RUNNING_POD = {
    "metadata": {
        "name": "kyrk-1-fghij",
        "creationTimestamp": "2026-09-14T07:03:00Z",
        "labels": {"batch.kubernetes.io/job-completion-index": "1"},
    },
    "status": {},
}

#: A volume's progress.json as the wrapper writes it (its
#: ``ProgressTracker.body``), with every field set so the schema sees a
#: populated row rather than only nulls.
WRAPPER_PROGRESS = {
    "stage": "stream",
    "pages_total": 638,
    "pages_done": 137,
    "pages_failed": 1,
    "last_page": "0137",
    "last_error": {"page": "0044", "error": "the worker thread died"},
    "errors": 3,
    "viewer_published": True,
    "started_at": "2026-09-14T07:02:00+00:00",
    "updated_at": "2026-09-14T07:31:00+00:00",
}

#: The API's clock when it read that file: 12 s after it was written.
#: Frozen, so the fixture's ageSeconds is the same on every run.
NOW = 1789371072.0  # 2026-09-14T07:31:12+00:00


def _bucket(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=WRAPPER_PROGRESS)


#: The read API's own reader, answered by a bucket that holds the file above
#: for every volume: the progress rows below are what `htrflow_web.progress`
#: makes of it, never a copy of its output that a renamed field would leave
#: green (2026-09-23 audit).
READER = progress.ProgressReader(httpx.Client(transport=httpx.MockTransport(_bucket)))


def _progress(base: str, volume_id: str, state: str) -> dict | None:
    """Progress for the rows that can have it, read through the reader."""
    with mock.patch.object(progress.time, "time", return_value=NOW):
        return READER.fetch(base, volume_id, state)


def _record(name: str) -> dict:
    return {
        "metadata": {
            "name": f"campaign-{name}",
            "namespace": "htr-test",
            "creationTimestamp": "2026-09-01T07:00:00Z",
            "labels": {**_LABELS, "htrflow.riksarkivet.se/campaign": name},
        },
        "data": CAMPAIGN_CM["data"],
    }


def _status(name: str, **data) -> dict:
    return {
        "metadata": {"name": f"campaign-{name}-status", "namespace": "htr-test"},
        "data": {
            "phase": "Succeeded",
            "volumesTotal": "3",
            "volumesDone": "2",
            "volumesFailed": "1",
            "startedAt": "2026-09-01T08:00:00Z",
            "finishedAt": "2026-09-01T10:00:00Z",
            "resultsBase": "https://results.example.org/htr-test/demo-v1",
            "failedVolumes": '[{"id":"vol2","reason":"manifest 404"}]',
            **data,
        },
    }


def build() -> dict:
    """Summaries and details covering every branch the page has to draw."""
    warmup_running = {"phase": "running"}
    warmup_failed = {
        "phase": "failed",
        "reason": {"stage": "warmup", "permanent": True, "error": "bad model id"},
    }

    live = projection.summarize(LIVE_JOB, CFG, warmup_running)
    gone = projection.record_summary(
        _record("gamla"), _status("gamla"), CFG, {"phase": "succeeded"}
    )
    # Nobody wrote down how this campaign ended: the Job was deleted while it
    # was still running, so the row says `Unknown` and the chip says so.
    unknown = projection.record_summary(
        _record("okand"),
        _status("okand", phase="Running", finishedAt=""),
        CFG,
        warmup_failed,
    )
    live_detail = projection.detail(
        LIVE_JOB,
        CAMPAIGN_CM,
        [FAILED_POD, RUNNING_POD],
        CFG,
        0,
        200,
        PIPELINE_CM,
        warmup=warmup_running,
        fetch_progress=_progress,
    )
    gone_detail = projection.record_detail(
        gone,
        _record("gamla"),
        _status("gamla"),
        CFG,
        PIPELINE_CM,
        fetch_progress=_progress,
    )
    # The rows of a campaign nobody recorded the ending of: `unknown` is a
    # volume state like any other, and the page has to draw it.
    unknown_detail = projection.record_detail(
        unknown,
        _record("okand"),
        _status("okand", phase="Running", finishedAt=""),
        CFG,
        PIPELINE_CM,
        fetch_progress=_progress,
    )
    return {
        "summaries": [live, gone, unknown],
        "details": [live_detail, gone_detail, unknown_detail],
    }


def main() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
