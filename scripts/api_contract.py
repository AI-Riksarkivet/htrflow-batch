#!/usr/bin/env python3
"""Generate the read API's contract fixture for the frontend's schemas.

The API is `packages/web` and the page that parses it is `frontend/`, and
nothing tied the two together: a field renamed on one side was found by
whoever next opened a campaign page (2026-09-14 audit). This writes what the
routes themselves answer -- the app is built with `create_app` over a fake
cluster and asked over HTTP, so headers, the version route, error bodies and
everything the routes add to the projection (warm-up matching, the reaped
window) are in it, not only what the projection functions return
(2026-09-23 test audit) -- to `frontend/src/lib/fixtures/api-contract.json`,
where a vitest parses every row with `jobSummarySchema`/`jobDetailSchema`.
A pytest re-runs this and fails if the committed file is not what it
prints, so the fixture cannot go stale either.

Regenerate with `make api-contract`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
from starlette.exceptions import StarletteDeprecationWarning

with warnings.catch_warnings():  # the test client's httpx; nothing to act on
    warnings.simplefilter("ignore", StarletteDeprecationWarning)
    from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "web" / "src"))

from htrflow_web import app, progress  # noqa: E402
from htrflow_web.kube import FIELD_MANAGER, ClusterUnavailable  # noqa: E402

FIXTURE = ROOT / "frontend" / "src" / "lib" / "fixtures" / "api-contract.json"

CFG = SimpleNamespace(
    public_results_base="https://results.example.org",
    internal_results_base="http://rustfs.htr-batch.svc:9000/htr-results",
    namespaces=("htr-test",),
)

#: What the version route reports: the tag is the app's argument, and the
#: package version is pinned here so a release does not change the fixture.
BATCH_VERSION = "v0.0.0-contract"
WEB_VERSION = "0.0.0"

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
        "uid": "uid-kyrk",
        "creationTimestamp": "2026-09-14T07:00:00Z",
        "labels": _LABELS,
    },
    "spec": {
        "completions": 3,
        "suspend": False,
        "template": {
            "spec": {
                "volumes": [
                    {"name": "campaign", "configMap": {"name": "campaign-kyrk"}},
                    {
                        "name": "pipeline",
                        "configMap": {"name": "htr-pipeline-demo-v1"},
                    },
                ]
            }
        },
    },
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


def _record(name: str, pipeline: str) -> dict:
    return {
        "metadata": {
            "name": f"campaign-{name}",
            "namespace": "htr-test",
            "creationTimestamp": "2026-09-01T07:00:00Z",
            "labels": {
                **_LABELS,
                "htrflow.riksarkivet.se/campaign": name,
                "htrflow.riksarkivet.se/pipeline": pipeline,
            },
        },
        "data": CAMPAIGN_CM["data"],
    }


def _status(name: str, pipeline: str, **data) -> dict:
    return {
        "metadata": {"name": f"campaign-{name}-status", "namespace": "htr-test"},
        "data": {
            "phase": "Succeeded",
            "volumesTotal": "3",
            "volumesDone": "2",
            "volumesFailed": "1",
            "startedAt": "2026-09-01T08:00:00Z",
            "finishedAt": "2026-09-01T10:00:00Z",
            "resultsBase": f"https://results.example.org/htr-test/{pipeline}",
            "failedVolumes": '[{"id":"vol2","reason":"manifest 404"}]',
            **data,
        },
    }


def _warmup(pipeline: str, **status) -> dict:
    return {
        "metadata": {
            "name": f"htr-warmup-{pipeline}",
            "namespace": "htr-test",
            "labels": {
                "app": "htrflow-warmup",
                "htrflow.riksarkivet.se/managed-by": "converter",
                "htrflow.riksarkivet.se/pipeline": pipeline,
            },
        },
        "status": status,
    }


WARMUP_POD = {
    "metadata": {"name": "htr-warmup-demo-v2-0", "namespace": "htr-test"},
    "status": {
        "containerStatuses": [
            {
                "name": "warmup",
                "state": {
                    "terminated": {
                        "exitCode": 13,
                        "message": json.dumps(
                            {
                                "stage": "warmup",
                                "permanent": True,
                                "error": "bad model id",
                            }
                        ),
                    }
                },
            }
        ]
    },
}

#: Every ConfigMap in the namespace, by name. `gamla` finished and was
#: reaped; nobody wrote down how `okand` ended -- its Job was deleted while
#: it was still running, so its row says `Unknown` and so do its volumes.
CONFIGMAPS = {
    cm["metadata"]["name"]: cm
    for cm in (
        CAMPAIGN_CM,
        _record("gamla", "demo-v0"),
        _status("gamla", "demo-v0"),
        _record("okand", "demo-v2"),
        _status("okand", "demo-v2", phase="Running", finishedAt=""),
    )
}


class ContractReader:
    """The cluster the fixture is read from: one live campaign whose warm-up
    is running, one reaped campaign whose warm-up succeeded, one reaped
    campaign nobody recorded the ending of, whose warm-up failed and says
    why. Answers the way ``kube.Reader`` does -- the record ConfigMaps are
    listed as metadata only, as the real list asks for them."""

    cfg = CFG

    def list_jobs(self) -> list[dict]:
        return [LIVE_JOB]

    def list_warmups(self) -> list[dict]:
        return [
            _warmup("demo-v1", active=1),
            _warmup("demo-v0", conditions=[{"type": "Complete", "status": "True"}]),
            _warmup("demo-v2", conditions=[{"type": "Failed", "status": "True"}]),
        ]

    def get_job(self, namespace: str, name: str) -> dict | None:
        return LIVE_JOB if name == "kyrk" else None

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        if name.startswith("htr-pipeline-"):
            return PIPELINE_CM
        return CONFIGMAPS.get(name)

    def list_configmaps(self) -> list[dict]:
        return [
            cm if name.endswith("-status") else {"metadata": cm["metadata"]}
            for name, cm in CONFIGMAPS.items()
            if name.startswith("campaign-") and name != "campaign-kyrk"
        ]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return {
            "kyrk": [FAILED_POD, RUNNING_POD],
            "htr-warmup-demo-v2": [WARMUP_POD],
        }.get(job_name, [])

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = FIELD_MANAGER
    ) -> str | None:
        return "uid-contract"  # the status write is not the contract


class UnavailableReader(ContractReader):
    """An API server that stopped answering: every route's 502."""

    def list_jobs(self) -> list[dict]:
        raise ClusterUnavailable("jobs: 403 Forbidden")


def _answer(client: TestClient, path: str, status: int = 200) -> httpx.Response:
    resp = client.get(path)
    if resp.status_code != status:
        raise SystemExit(f"GET {path}: {resp.status_code}, not {status}")
    return resp


def _error(reader, path: str, status: int) -> dict:
    with tempfile.TemporaryDirectory() as site:
        client = TestClient(create(reader, site), raise_server_exceptions=False)
        return {"status": status, "body": _answer(client, path, status).json()}


def create(reader, site: str):
    """The app over ``reader``, with an empty site and the read API's own
    ProgressReader over a bucket that holds WRAPPER_PROGRESS for every
    volume: the progress rows are what `htrflow_web.progress` makes of it,
    never a copy of its output that a renamed field would leave green
    (2026-09-23 audit). A new one per app, so no answer is cached across
    builds."""
    bucket = httpx.Client(transport=httpx.MockTransport(_bucket))
    return app.create_app(
        reader,
        static_dir=site,
        batch_version=BATCH_VERSION,
        progress=progress.ProgressReader(bucket),
    )


def build() -> dict:
    """What the routes answer, covering every branch the page has to draw."""
    with (
        mock.patch.object(progress.time, "time", return_value=NOW),
        mock.patch.object(app, "WEB_VERSION", WEB_VERSION),
        tempfile.TemporaryDirectory() as site,
    ):
        client = TestClient(create(ContractReader(), site))
        jobs = _answer(client, "/api/v1/jobs")
        details = [
            _answer(client, f"/api/v1/jobs/htr-test/{name}").json()
            for name in ("kyrk", "gamla", "okand")
        ]
        version = _answer(client, "/api/v1/version").json()
        errors = [
            _error(ContractReader(), "/api/v1/jobs/htr-test/nonesuch", 404),
            _error(UnavailableReader(), "/api/v1/jobs", 502),
            _error(app.NoCluster(), "/api/v1/jobs", 503),
        ]
    return {
        "summaries": jobs.json(),
        "reapedTotal": jobs.headers["X-Reaped-Total"],
        "details": details,
        "version": version,
        "errors": errors,
    }


def main() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
