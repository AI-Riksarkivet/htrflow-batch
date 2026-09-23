"""FastAPI wiring tests: fake reader, no cluster (docs: task-4-brief)."""

from __future__ import annotations

import asyncio
import copy
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from kubernetes.client import ApiException

from htrflow_web import projection
from htrflow_web.app import (
    DNS_1123,
    REAPED_SHOWN,
    RECORD_WRITES_PER_REQUEST,
    SECURITY_HEADERS,
    NoCluster,
    create_app,
)
from htrflow_web.kube import (
    FIELD_MANAGER,
    ApplyConflict,
    ClusterUnavailable,
)
from htrflow_web.projection import FAILURES_MANAGER

JOB = {
    "metadata": {
        "name": "kyrk",
        "namespace": "htr-test",
        "uid": "uid-kyrk",
        "creationTimestamp": "2026-01-01T00:00:00Z",
        "labels": {
            "app": "htrflow-batch",
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/campaign": "kyrk",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "spec": {
        "completions": 2,
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
        "completedIndexes": "0",
        "failedIndexes": "",
        "conditions": [],
    },
}

CONFIGMAP = {
    "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
    "data": {
        "volumes.txt": "vol0\thttps://iiif.example.org/vol0/manifest\n"
        "vol1\thttps://iiif.example.org/vol1/manifest\n"
    },
}

PIPELINE_CONFIGMAP = {
    "metadata": {"name": "htr-pipeline-demo-v1", "namespace": "htr-test"},
    "data": {"pipeline.yaml": "steps:\n- step: Segmentation\n"},
}


class FakeReader:
    cfg = SimpleNamespace(
        public_results_base="https://results.example.org",
        namespaces=("htr-test",),
    )

    def list_jobs(self) -> list[dict]:
        return [JOB]

    def list_warmups(self) -> list[dict]:
        return []

    def get_job(self, namespace: str, name: str) -> dict | None:
        if (
            namespace == JOB["metadata"]["namespace"]
            and name == JOB["metadata"]["name"]
        ):
            return JOB
        return None

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return {
            "campaign-kyrk": CONFIGMAP,
            "htr-pipeline-demo-v1": PIPELINE_CONFIGMAP,
        }.get(name)

    def list_configmaps(self) -> list[dict]:
        return []  # no record ConfigMaps: only the Job answers for a campaign

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return []

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = FIELD_MANAGER
    ) -> str | None:
        pass  # RecordingReader keeps what is written; this fake drops it


class FakeProgress:
    """Stands in for the bucket: create_app's real one would make an HTTP
    call per volume row, and these tests have no bucket to answer it."""

    def __init__(self, known: dict | None = None) -> None:
        self.known = known or {}

    def fetch(self, results_base: str, volume_id: str, state: str) -> dict | None:
        return self.known.get(volume_id)

    def cached(
        self, results_base: str, volume_id: str, state: str
    ) -> tuple[bool, dict | None]:
        return False, None


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(FakeReader(), progress=FakeProgress()))


def test_healthz(client: TestClient):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


class _Hung(FakeReader):
    """A reader whose API server has stopped answering mid-request."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def get_job(self, namespace: str, name: str) -> dict | None:
        self.release.wait(10)
        return super().get_job(namespace, name)


def test_healthz_answers_while_every_worker_is_stuck():
    """Every sync route runs on one shared thread pool. With /healthz on it
    too, requests stuck on a hung API server queued the probe behind them,
    readiness failed, and the only replica left the Service (2026-09-23
    audit). Here the pool has one thread and a request is holding it."""
    reader = _Hung()
    app = create_app(reader, progress=FakeProgress())

    async def scenario() -> int:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            stuck = asyncio.ensure_future(c.get("/api/v1/jobs/htr-test/kyrk"))
            await asyncio.sleep(0.1)
            try:
                with anyio.fail_after(2):
                    status = (await c.get("/healthz")).status_code
            finally:
                reader.release.set()
                await stuck
        return status

    assert asyncio.run(scenario()) == 200


def test_version(client: TestClient):
    """What the operator deployed (the image's tag, which the page's header
    shows) and, beside it, the package that is actually answering. The
    fixture app is given no tag, so it reports the dockerfile's default."""
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"version", "web"}
    assert body["version"] == "dev"
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", body["web"])


def test_version_reports_the_deployed_tag():
    app = create_app(FakeReader(), batch_version="v0.2.0")
    assert TestClient(app).get("/api/v1/version").json()["version"] == "v0.2.0"


def test_list_jobs_shape(client: TestClient):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "namespace": "htr-test",
            "name": "kyrk",
            "campaign": "kyrk",
            "pipeline": "demo-v1",
            "phase": "Running",
            "counts": {"total": 2, "active": 1, "done": 1, "failed": 0},
            "suspended": False,
            "createdAt": "2026-01-01T00:00:00Z",
            "startedAt": None,
            "finishedAt": None,
            "resultsBase": "https://results.example.org/htr-test/demo-v1",
            "warmup": {"phase": "missing"},
            "jobGone": False,
        }
    ]


def test_job_detail_shape(client: TestClient):
    resp = client.get("/api/v1/jobs/htr-test/kyrk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["namespace"] == "htr-test"
    assert body["name"] == "kyrk"
    assert body["phase"] == "Running"
    assert len(body["volumes"]) == 2
    assert body["volumes"][0]["id"] == "vol0"
    assert body["volumes"][0]["state"] == "done"
    assert body["failures"] == []


def test_job_detail_carries_each_volume_progress_and_the_campaign_total():
    """C13/C11: the row says 137 of 638, the card sums what it knows."""
    progress = FakeProgress(
        {
            "vol0": {
                "done": 137,
                "total": 638,
                "failed": 1,
                "lastPage": "0137",
                "stage": "stream",
                "updatedAt": "2026-09-08T09:31:00+00:00",
                "lastError": {"page": "0044", "error": "the worker thread died"},
                "errors": 2,
                "viewerPublished": True,
            }
        }
    )
    client = TestClient(create_app(FakeReader(), progress=progress))
    body = client.get("/api/v1/jobs/htr-test/kyrk").json()
    assert body["volumes"][0]["progress"]["done"] == 137
    assert body["volumes"][1]["progress"] is None
    assert (body["pagesDone"], body["pagesTotal"]) == (137, 638)
    assert (body["pagesFailed"], body["errors"]) == (1, 2)
    assert body["lastError"]["volume"] == "vol0"
    assert body["lastError"]["logUrl"].endswith("/status/logs/demo-v1/vol0.txt")


def test_job_detail_carries_the_pipeline_steps_and_yaml(client: TestClient):
    """Both ConfigMaps are read for one detail response: the campaign's
    volumes.txt and the pipeline's pipeline.yaml."""
    body = client.get("/api/v1/jobs/htr-test/kyrk").json()
    assert body["pipelineSteps"] == ["Segmentation"]
    assert body["pipelineYaml"] == "steps:\n- step: Segmentation\n"


def test_job_detail_carries_the_latest_volume(client: TestClient):
    body = client.get("/api/v1/jobs/htr-test/kyrk").json()
    assert body["latest"]["id"] == "vol0"  # completedIndexes "0", none active


def test_job_detail_paging(client: TestClient):
    resp = client.get("/api/v1/jobs/htr-test/kyrk?offset=1&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["index"] for v in body["volumes"]] == [1]


def test_unknown_job_404(client: TestClient):
    resp = client.get("/api/v1/jobs/htr-test/nope")
    assert resp.status_code == 404


def test_post_not_allowed(client: TestClient):
    resp = client.post("/api/v1/jobs", json={})
    assert resp.status_code == 405


WARMUP_JOB_FAILED = {
    "metadata": {
        "name": "htr-warmup-demo-v1",
        "namespace": "htr-test",
        "labels": {
            "app": "htrflow-warmup",
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "status": {"active": 0, "conditions": [{"type": "Failed", "status": "True"}]},
}

WARMUP_JOB_SUCCEEDED = {
    **WARMUP_JOB_FAILED,
    "status": {"active": 0, "conditions": [{"type": "Complete", "status": "True"}]},
}

WARMUP_POD = {
    "metadata": {"name": "htr-warmup-demo-v1-0", "namespace": "htr-test"},
    "status": {
        "containerStatuses": [
            {
                "name": "warmup",
                "state": {
                    "terminated": {
                        "exitCode": 13,
                        "message": (
                            '{"stage": "warmup", "permanent": true,'
                            ' "error": "unknown model class Yolo9"}'
                        ),
                    }
                },
            }
        ]
    },
}


class FailedWarmupReader(FakeReader):
    def list_warmups(self) -> list[dict]:
        return [WARMUP_JOB_FAILED]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return [WARMUP_POD] if job_name == "htr-warmup-demo-v1" else []


class SucceededWarmupReader(FakeReader):
    def list_warmups(self) -> list[dict]:
        return [WARMUP_JOB_SUCCEEDED]


class FailedWarmupNoPodsReader(FakeReader):
    """A failed warm-up Job whose pods are already gone (GC, node loss) --
    `_warmup_status` must not crash calling `newest([])`."""

    def list_warmups(self) -> list[dict]:
        return [WARMUP_JOB_FAILED]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return []


def test_list_jobs_carries_a_failed_warmups_reason():
    """No warm-up log exists (Task 28) -- the reason is the only way a bad
    model id reaches a reader, so the list row must carry it, not just the
    phase."""
    client = TestClient(create_app(FailedWarmupReader()))
    body = client.get("/api/v1/jobs").json()
    assert body[0]["warmup"] == {
        "phase": "failed",
        "reason": {
            "stage": "warmup",
            "permanent": True,
            "error": "unknown model class Yolo9",
        },
    }


def test_list_jobs_carries_a_succeeded_warmup_with_no_reason():
    client = TestClient(create_app(SucceededWarmupReader()))
    body = client.get("/api/v1/jobs").json()
    assert body[0]["warmup"] == {"phase": "succeeded"}


def test_list_jobs_survives_a_failed_warmup_with_no_pods():
    client = TestClient(create_app(FailedWarmupNoPodsReader()))
    body = client.get("/api/v1/jobs").json()
    assert body[0]["warmup"] == {"phase": "failed"}


JOB2 = {**JOB, "metadata": {**JOB["metadata"], "name": "kyrk2"}}
JOB_OTHER_NS = {**JOB, "metadata": {**JOB["metadata"], "namespace": "htr-other"}}


class TwoCampaignsOneFailedWarmupReader(FailedWarmupReader):
    """Same namespace + pipeline label on both campaigns, so both match the
    one failed warm-up -- list_pods must be called once, not per campaign."""

    def __init__(self) -> None:
        self.list_pods_calls = 0

    def list_jobs(self) -> list[dict]:
        return [JOB, JOB2, JOB_OTHER_NS]

    def list_warmups(self) -> list[dict]:
        w = super().list_warmups()[0]
        other = {**w, "metadata": {**w["metadata"], "namespace": "htr-other"}}
        return [w, other]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        self.list_pods_calls += 1
        pods = super().list_pods(namespace, job_name)
        if namespace != "htr-other":
            return pods
        import copy

        pods = copy.deepcopy(pods)
        term = pods[0]["status"]["containerStatuses"][0]["state"]["terminated"]
        term["message"] = term["message"].replace("Yolo9", "Other9")
        return pods


def test_list_jobs_calls_list_pods_once_per_failed_warmup_per_namespace():
    """Two campaigns in one namespace share one call; a same-named warm-up
    in another namespace is its own call with its own reason."""
    reader = TwoCampaignsOneFailedWarmupReader()
    client = TestClient(create_app(reader))
    body = client.get("/api/v1/jobs").json()
    assert len(body) == 3
    assert all(row["warmup"]["phase"] == "failed" for row in body)
    assert reader.list_pods_calls == 2
    by_ns = {row["namespace"]: row["warmup"]["reason"]["error"] for row in body}
    assert by_ns == {
        "htr-test": "unknown model class Yolo9",
        "htr-other": "unknown model class Other9",
    }


def test_job_detail_carries_the_warmup_field_too():
    """Task 28: the detail response inherits `warmup` from the same
    matching the list row does -- not just a `missing` default."""
    client = TestClient(create_app(FailedWarmupReader()))
    body = client.get("/api/v1/jobs/htr-test/kyrk").json()
    assert body["warmup"]["phase"] == "failed"
    assert body["warmup"]["reason"]["error"] == "unknown model class Yolo9"


def test_the_only_mutating_call_is_the_campaign_record():
    """RBAC is get/list/watch on jobs and pods, and create/patch on
    ConfigMaps for the one write there is: the per-campaign status ConfigMap
    (B76). Nothing here may ever delete, or write a Job or a Pod."""
    src = Path(__file__).parent.parent / "src" / "htrflow_web"
    offenders = []
    pattern = re.compile(r"\.(create_|patch_|delete_|replace_)\w*\(")
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line) and "patch_namespaced_config_map" not in line:
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []


# --- the read API writes the status ConfigMap (B76) ----------------------


class RecordingReader(FakeReader):
    """FakeReader that also answers the two ConfigMap calls the record
    needs, and keeps what was written to it."""

    def __init__(self, live: list[dict] | None = None) -> None:
        self.live = live or []
        self.written: list[dict] = []
        self.forced: list[bool] = []
        self.managers: list[str] = []

    def list_configmaps(self) -> list[dict]:
        return self.live

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        for cm in self.live:
            if cm["metadata"]["name"] == name:
                return cm
        return super().get_configmap(namespace, name)

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = "htrflow-web"
    ) -> str | None:
        # The API server refuses a name no object could carry, and a fake
        # that accepts one proves nothing about what the cluster would do
        # with the names this package builds (2026-09-14 audit).
        meta = body["metadata"]
        for field in ("name", "namespace"):
            value = meta[field]
            assert len(value) <= 63, f"{field} is not a DNS-1123 label: {value!r}"
            assert DNS_1123.fullmatch(value), f"{field} is not DNS-1123: {value!r}"
        self.written.append(body)
        self.forced.append(force)
        self.managers.append(manager)
        return "uid-recorded"


def _status_of(reader: RecordingReader) -> dict:
    assert len(reader.written) == 1
    return reader.written[0]


def _written_by(reader: RecordingReader, manager: str) -> dict:
    """The one write this request made as ``manager``."""
    (body,) = [b for b, m in zip(reader.written, reader.managers) if m == manager]
    return body


def test_listing_campaigns_writes_what_it_observed(capsys):
    reader = RecordingReader()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get("/api/v1/jobs").status_code == 200
    cm = _status_of(reader)
    assert cm["metadata"]["name"] == "campaign-kyrk-status"
    assert cm["data"]["phase"] == "Running"
    assert cm["data"]["volumesDone"] == "1"
    assert "failedVolumes" not in cm["data"]


def test_a_record_that_already_says_this_is_not_written_again():
    """One PATCH per request per campaign would be a write on every poll of
    an idle status page. Only a changed body is sent."""
    reader = RecordingReader()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    client.get("/api/v1/jobs")
    reader.live = [_status_of(reader)]
    reader.written.clear()
    client.get("/api/v1/jobs")
    assert reader.written == []


def test_the_detail_endpoint_records_the_failed_volumes():
    """Under a field manager of their own, after the summary: the summary
    creates the record with its labels, and the failures land on it."""
    reader = RecordingReader()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get("/api/v1/jobs/htr-test/kyrk").status_code == 200
    assert reader.managers == [FIELD_MANAGER, FAILURES_MANAGER]
    summary, failures = reader.written
    assert "failedVolumes" not in summary["data"]
    assert summary["metadata"]["labels"]
    assert failures["data"] == {
        "failedVolumes": "[]",
        "failedVolumesJobUid": "uid-kyrk",
    }
    assert reader.forced[1] is True, "no other manager has a say in the field"


def test_the_list_route_never_sends_the_failed_volumes():
    """It re-sent the value it had read, under the manager the detail route
    wrote with, so a list request could apply it over reasons a detail
    request had just added (2026-09-23 audit). The field is left to the
    failures manager, which keeps it when this one says nothing of it."""
    stored = {
        "metadata": {
            "name": "campaign-kyrk-status",
            "namespace": "htr-test",
            "managedFields": [_owns(FAILURES_MANAGER, "failedVolumes")],
        },
        "data": {"failedVolumes": '[{"id":"vol1","reason":"boom"}]'},
    }
    reader = RecordingReader([stored])
    client = TestClient(create_app(reader, progress=FakeProgress()))
    client.get("/api/v1/jobs")
    assert "failedVolumes" not in _status_of(reader)["data"]
    assert reader.managers == [FIELD_MANAGER]


def test_a_record_from_before_the_split_keeps_its_failures_until_handed_over():
    """A record whose failedVolumes the summary manager still owns: leaving
    the field out would release it, and a field nobody owns is deleted. The
    stored value is sent back as it is until the failures manager takes the
    field over."""
    kept = '[{"id":"vol1","reason":"boom"}]'
    stored = {
        "metadata": {
            "name": "campaign-kyrk-status",
            "namespace": "htr-test",
            "managedFields": [_owns(FIELD_MANAGER, "phase", "failedVolumes")],
        },
        "data": {"phase": "Queued", "failedVolumes": kept},
    }
    reader = RecordingReader([stored])
    client = TestClient(create_app(reader, progress=FakeProgress()))
    client.get("/api/v1/jobs")
    assert _status_of(reader)["data"]["failedVolumes"] == kept


def test_a_failed_write_is_logged_and_the_request_still_answers(caplog):
    client = TestClient(create_app(_Refusing(), progress=FakeProgress()))
    assert client.get("/api/v1/jobs").status_code == 200
    assert "campaign-kyrk-status" in caplog.text


def test_site_only_mode_answers_honestly_instead_of_writing():
    """No cluster to write to -- and NoCluster's 503 must not escape from
    inside the record path as a 500. Driven through the route: asking the
    class whether it has the attribute only re-states how the code is
    written (2026-09-14 audit)."""
    client = TestClient(create_app(NoCluster(), progress=FakeProgress()))
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 503
    assert "HTRFLOW_WEB_SITE_ONLY" in resp.json()["detail"]


def _owns(manager: str, *keys: str) -> dict:
    return {
        "manager": manager,
        "operation": "Apply",
        "fieldsV1": {"f:data": {f"f:{k}": {} for k in keys}},
    }


REAPED_RECORD = {
    "metadata": {
        "name": "campaign-gamla",
        "namespace": "htr-test",
        "creationTimestamp": "2025-12-01T00:00:00Z",
        "labels": {
            "htrflow.riksarkivet.se/campaign": "gamla",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "data": {
        "volumes.txt": (
            "vol9\thttps://iiif.example.org/vol9/manifest\n"
            "vol8\thttps://iiif.example.org/vol8/manifest\n"
            "vol7\thttps://iiif.example.org/vol7/manifest\n"
        )
    },
}

REAPED_STATUS = {
    "metadata": {"name": "campaign-gamla-status", "namespace": "htr-test"},
    "data": {
        "phase": "Succeeded",
        "volumesTotal": "4",
        "volumesDone": "4",
        "volumesFailed": "0",
        "startedAt": "2025-12-01T01:00:00Z",
        "finishedAt": "2025-12-01T05:00:00Z",
        "resultsBase": "https://results.example.org/htr-test/demo-v1",
        "failedVolumes": '[{"id":"vol8","reason":"manifest 404"}]',
    },
}


def _reaped_reader() -> RecordingReader:
    return RecordingReader([REAPED_RECORD, REAPED_STATUS])


def test_a_campaign_whose_job_is_gone_still_has_a_row():
    """The Job was reaped by its TTL; the two ConfigMaps are the campaign
    now, and the list is where an operator looks for it (B76)."""
    client = TestClient(create_app(_reaped_reader(), progress=FakeProgress()))
    body = client.get("/api/v1/jobs").json()
    by_name = {row["name"]: row for row in body}
    assert set(by_name) == {"kyrk", "gamla"}
    assert by_name["kyrk"]["jobGone"] is False
    gone = by_name["gamla"]
    assert gone["jobGone"] is True
    assert gone["phase"] == "Succeeded"
    assert gone["counts"] == {"total": 4, "active": 0, "done": 4, "failed": 0}
    assert gone["finishedAt"] == "2025-12-01T05:00:00Z"
    assert body[0]["name"] == "kyrk", "newest first, reaped rows included"


def _many_reaped(n: int) -> RecordingReader:
    """``n`` campaigns whose Jobs are gone, one a day, newest last."""
    cms: list[dict] = []
    for i in range(n):
        name = f"gamla{i}"
        cms.append(
            {
                "metadata": {
                    **REAPED_RECORD["metadata"],
                    "name": f"campaign-{name}",
                    "creationTimestamp": f"2025-11-{i + 1:02d}T00:00:00Z",
                },
            }
        )
        cms.append(
            {
                "metadata": {
                    "name": f"campaign-{name}-status",
                    "namespace": "htr-test",
                },
                "data": REAPED_STATUS["data"],
            }
        )
    return RecordingReader(cms)


def test_the_list_carries_only_the_newest_reaped_campaigns():
    """Records have no TTL, so every campaign ever run was a row, and the
    page a card with its own requests for each (2026-09-23 audit). Every
    live Job is still listed; of the reaped, the newest few, and a header
    saying how many there are in all."""
    client = TestClient(create_app(_many_reaped(25), progress=FakeProgress()))
    resp = client.get("/api/v1/jobs")
    gone = [row["name"] for row in resp.json() if row["jobGone"]]
    assert len(gone) == REAPED_SHOWN
    assert gone[0] == "gamla24" and gone[-1] == f"gamla{25 - REAPED_SHOWN}"
    assert resp.headers["X-Reaped-Total"] == "25"
    assert any(row["name"] == "kyrk" for row in resp.json()), "live Jobs: all"


def test_older_reaped_campaigns_are_asked_for_by_count():
    client = TestClient(create_app(_many_reaped(25), progress=FakeProgress()))
    resp = client.get("/api/v1/jobs?reaped=24")
    assert sum(row["jobGone"] for row in resp.json()) == 24
    none = client.get("/api/v1/jobs?reaped=0")
    assert [row["name"] for row in none.json()] == ["kyrk"]
    assert none.headers["X-Reaped-Total"] == "25"


def test_the_reaped_window_ranks_by_when_a_campaign_ended():
    """A long campaign created weeks ago and reaped today is news; ranked by
    its record's creation date it hid behind "show older" (2026-09-23
    review). When it finished decides, and creation only when that was
    never written."""
    reader = _many_reaped(25)
    for cm in reader.live:
        if cm["metadata"]["name"] == "campaign-gamla0-status":
            cm["data"] = {**cm["data"], "finishedAt": "2026-09-23T10:00:00+02:00"}
        elif cm["metadata"]["name"].endswith("-status"):
            cm["data"] = {**cm["data"], "finishedAt": ""}
    client = TestClient(create_app(reader, progress=FakeProgress()))
    gone = [
        r["name"] for r in client.get("/api/v1/jobs?reaped=2").json() if r["jobGone"]
    ]
    assert set(gone) == {"gamla0", "gamla24"}


@pytest.mark.parametrize("bad", ["-1", "100001", "x"])
def test_a_reaped_count_out_of_range_is_refused(bad: str):
    client = TestClient(create_app(_many_reaped(1), progress=FakeProgress()))
    assert client.get(f"/api/v1/jobs?reaped={bad}").status_code == 422


def test_the_list_route_draws_a_reaped_row_from_metadata_alone():
    """The record is listed as PartialObjectMetadata (2026-09-14 audit), so
    `data` -- the campaign's whole volume list -- is simply not there. A row
    that needed it would break here rather than on a real backfill."""
    metadata_only = {"metadata": REAPED_RECORD["metadata"]}
    assert "data" not in metadata_only
    client = TestClient(
        create_app(
            RecordingReader([metadata_only, REAPED_STATUS]), progress=FakeProgress()
        )
    )
    rows = {row["name"]: row for row in client.get("/api/v1/jobs").json()}
    assert rows["gamla"]["jobGone"] is True
    assert rows["gamla"]["counts"]["done"] == 4


def test_a_campaign_configmap_with_no_record_beside_it_is_not_a_row():
    """Never observed by this API: it is being applied right now, and the
    Job will say more than a guess would."""
    client = TestClient(create_app(RecordingReader([REAPED_RECORD])))
    assert [row["name"] for row in client.get("/api/v1/jobs").json()] == ["kyrk"]


def test_the_detail_of_a_reaped_campaign_still_opens_its_volumes():
    """The Job is gone, but manifest.json, iiif.json, alto/ and the run log
    are all still in the bucket -- and this route answered with no rows at
    all, so nobody could open any of them (R1, the product owner,
    2026-09-14)."""
    client = TestClient(create_app(_reaped_reader(), progress=FakeProgress()))
    resp = client.get("/api/v1/jobs/htr-test/gamla")
    assert resp.status_code == 200
    body = resp.json()
    assert body["jobGone"] is True
    assert [v["id"] for v in body["volumes"]] == ["vol9", "vol8", "vol7"]
    base = "https://results.example.org/htr-test/demo-v1"
    assert body["volumes"][0]["iiifUrl"] == f"{base}/vol9/iiif.json"
    assert body["volumes"][0]["altoPrefix"] == f"{base}/vol9/alto/"
    assert body["volumes"][0]["logUrl"].endswith("/status/logs/demo-v1/vol9.txt")
    assert [v["state"] for v in body["volumes"]] == ["done", "failed", "done"]
    assert body["volumes"][1]["reason"]["error"] == "manifest 404"
    assert [v["id"] for v in body["failures"]] == ["vol8"]
    assert body["latest"]["id"] == "vol7", "the last volume that finished"


def test_a_campaign_with_neither_job_nor_record_is_still_a_404():
    client = TestClient(create_app(_reaped_reader(), progress=FakeProgress()))
    assert client.get("/api/v1/jobs/htr-test/nonesuch").status_code == 404


def test_a_later_detail_request_does_not_erase_the_failed_volumes():
    """End to end over the route: the pods are gone, so this response has no
    reasons -- and the record keeps the ones it already had (B76 review)."""
    kept = '[{"id":"vol1","reason":"manifest 404"}]'
    reader = RecordingReader(
        [
            {
                "metadata": {
                    "name": "campaign-kyrk-status",
                    "namespace": "htr-test",
                },
                "data": {"phase": "Running", "failedVolumes": kept},
            }
        ]
    )
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get("/api/v1/jobs/htr-test/kyrk").status_code == 200
    assert _written_by(reader, FAILURES_MANAGER)["data"]["failedVolumes"] == kept


def test_failed_volumes_land_when_apply_owns_every_other_field():
    """apply recorded the ending, with a results base a slash apart from
    this API's and no campaign label on the Job it read. Sending those
    fields back unforced was a 409 on every poll, and the failure reasons
    were never written (3081). Only what apply does not own is sent."""
    stored = {
        "metadata": {
            "name": "campaign-kyrk-status",
            "namespace": "htr-test",
            "uid": "uid-cm",
            "labels": {"htrflow.riksarkivet.se/campaign": ""},
            "managedFields": [
                {
                    "manager": "htrflow-campaigns",
                    "operation": "Apply",
                    "fieldsV1": {
                        "f:data": {
                            f"f:{k}": {}
                            for k in (
                                "phase", "volumesTotal", "volumesDone",
                                "volumesFailed", "startedAt", "finishedAt",
                                "resultsBase", "jobUid",
                            )
                        }
                    },
                }
            ],
        },
        "data": {
            "phase": "Running",
            "jobUid": "uid-kyrk",
            "resultsBase": "http://x//htr-test/demo-v1",
        },
    }  # fmt: skip
    reader = RecordingReader([stored])
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get("/api/v1/jobs/htr-test/kyrk").status_code == 200
    written = _status_of(reader)
    assert written["data"] == {"failedVolumes": "[]", "failedVolumesJobUid": "uid-kyrk"}
    assert "labels" not in written["metadata"]
    assert reader.managers == [FAILURES_MANAGER]


def test_a_recreated_job_does_not_inherit_the_last_runs_record():
    """Same name, new Job: the record of the old one -- its failures, its
    finishedAt -- is not this run's (3075)."""
    stored = {
        "metadata": {"name": "campaign-kyrk-status", "namespace": "htr-test"},
        "data": {
            "phase": "PartiallyFailed",
            "jobUid": "uid-old",
            "finishedAt": "2026-09-08T10:00:00Z",
            "failedVolumes": '[{"id":"vol1","reason":"manifest 404"}]',
        },
    }
    reader = RecordingReader([stored])
    client = TestClient(create_app(reader, progress=FakeProgress()))
    client.get("/api/v1/jobs")
    written = _status_of(reader)["data"]
    assert written["jobUid"] == "uid-kyrk"
    assert written["phase"] == "Running"
    assert written["finishedAt"] == ""
    # Left out: the old run's list is tied to the old run and read as
    # nobody's (test_a_recreated_jobs_record_never_counts_...).
    assert "failedVolumes" not in written
    assert reader.forced == [False]


class _Refusing(RecordingReader):
    """Every write refused, the way an unrenewed RBAC grant refuses them."""

    def __init__(self, live=None) -> None:
        super().__init__(live)
        self.attempts = 0

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = "htrflow-web"
    ) -> str | None:
        self.attempts += 1
        raise RuntimeError("configmaps is forbidden")


class _Contested(RecordingReader):
    """Every write meets another field manager, the way `apply`'s terminal
    record does once a campaign is over."""

    def __init__(self, live=None) -> None:
        super().__init__(live)
        self.attempts = 0

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = "htrflow-web"
    ) -> str | None:
        self.attempts += 1
        raise ApplyConflict(body["metadata"]["name"])


def test_a_contested_record_is_not_treated_as_a_refused_namespace(caplog):
    """A 409 says another manager owns the field, not that this service's
    grant went away -- so it must not stop the writes for ten minutes, and
    it is not worth a line in the log either (2026-09-14 review)."""
    reader = _Contested()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    for _ in range(3):
        assert client.get("/api/v1/jobs").status_code == 200
    assert reader.attempts == 3, "still tried on every poll"
    # Not `caplog.text == ""`: the test client's own httpx logs a line per
    # request under CI's log level. What must not be there is this
    # service's "could not write" warning.
    assert "could not write" not in caplog.text


class ManyReader(RecordingReader):
    """More campaigns in one namespace than one request may write for."""

    def list_jobs(self) -> list[dict]:
        return [
            {
                **JOB,
                "metadata": {**JOB["metadata"], "name": f"kyrk{i}"},
            }
            for i in range(RECORD_WRITES_PER_REQUEST + 5)
        ]


def test_a_refused_write_is_logged_once_per_namespace(caplog):
    """A 403 fires for every campaign on every poll: that is a log nobody
    can read and a page nobody can debug. Said once, then remembered."""
    reader = _Refusing()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    for _ in range(3):
        assert client.get("/api/v1/jobs").status_code == 200
    assert caplog.text.count("forbidden") == 1


def test_a_refused_namespace_is_left_alone_for_the_cooldown():
    """An unrenewed RBAC grant refuses every campaign on every poll, and
    each refusal is a server-side apply on the request's critical path --
    twenty round trips per page load, for ever (2026-09-14 audit)."""
    reader = _Refusing()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    for _ in range(3):
        assert client.get("/api/v1/jobs").status_code == 200
    assert reader.attempts == 1, "one refusal is enough to stop trying"


def test_the_cooldown_expires_and_the_grant_is_tried_again(monkeypatch):
    """A grant that was renewed must start working again on its own."""
    from htrflow_web import app as app_mod

    clock = [0.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: clock[0])
    reader = _Refusing()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    client.get("/api/v1/jobs")
    clock[0] = app_mod.REFUSAL_COOLDOWN + 1
    client.get("/api/v1/jobs")
    assert reader.attempts == 2


def test_no_request_writes_more_records_than_its_cap():
    """The write is on the request's critical path -- one SSA round trip per
    campaign. A namespace of hundreds must not turn one page load into
    hundreds of sequential writes; the rest are written by the next poll."""
    reader = ManyReader()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    body = client.get("/api/v1/jobs").json()
    assert len(body) == RECORD_WRITES_PER_REQUEST + 5, "every row still answers"
    assert len(reader.written) == RECORD_WRITES_PER_REQUEST


# --- the cluster not answering is a 502, not a bare 500 (F1) --------------


class _Unavailable(FakeReader):
    """Everything the API server could say that is not a 404: a 403 after an
    RBAC change, a 429, a connection that timed out."""

    def list_jobs(self) -> list[dict]:
        raise ClusterUnavailable("jobs: 403 Forbidden")

    def get_job(self, namespace: str, name: str) -> dict | None:
        raise ClusterUnavailable("jobs/kyrk: timed out")


@pytest.mark.parametrize("path", ["/api/v1/jobs", "/api/v1/jobs/htr-test/kyrk"])
def test_a_cluster_that_does_not_answer_is_a_502_with_the_headers(path: str):
    """A bare exception leaves Starlette's own plain-text 500, which never
    passes through the header middleware: the browser gets a page with no
    nosniff, no Referrer-Policy and no frame-ancestors at all."""
    client = TestClient(
        create_app(_Unavailable(), progress=FakeProgress()),
        raise_server_exceptions=False,
    )
    resp = client.get(path)
    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"].count(".") <= 1, "one sentence for the reader"
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers[name] == value


class _Broken(FakeReader):
    """A bug: something no handler knows about escapes a route."""

    def list_jobs(self) -> list[dict]:
        raise KeyError("a bug, not the cluster")


def test_an_unexpected_error_is_a_500_that_still_carries_the_headers(caplog):
    """Any exception escaping a route was answered by Starlette outside the
    header middleware -- a plain-text 500 with no nosniff and no
    frame-ancestors (2026-09-23 audit). Logged in full, never quoted."""
    client = TestClient(
        create_app(_Broken(), progress=FakeProgress()),
        raise_server_exceptions=False,
    )
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert "a bug" not in resp.text
    assert "a bug, not the cluster" in caplog.text
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers[name] == value


def test_the_502_never_quotes_the_client_error():
    """The API server's own message names namespaces, verbs and identities;
    the page says what the reader can do instead."""
    client = TestClient(
        create_app(_Unavailable(), progress=FakeProgress()),
        raise_server_exceptions=False,
    )
    assert "403" not in client.get("/api/v1/jobs").text


# --- the detail route only answers for names that could exist (F8) -------


class Counting(FakeReader):
    """Records every read, so a test can assert one never happened."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def get_job(self, namespace: str, name: str) -> dict | None:
        self.asked.append((namespace, name))
        return super().get_job(namespace, name)

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        self.asked.append((namespace, name))
        return super().get_configmap(namespace, name)


@pytest.mark.parametrize(
    ("namespace", "name"),
    [
        ("htr-test", "Kyrk"),  # DNS-1123 is lower-case
        ("htr-test", "kyrk.part1"),  # a label carries no dots
        ("htr-test", "-kyrk"),
        ("htr-test", "kyrk-"),
        ("htr-test", "kyrk_1"),
        ("htr-test", "k" * 64),
        ("htr-test", "kyrk%2F..%2Fx"),
        ("HTR-TEST", "kyrk"),
        ("kube-system", "kyrk"),  # a real namespace, not one this API serves
    ],
)
def test_a_campaign_this_api_could_not_have_is_a_404_before_any_read(
    namespace: str, name: str
):
    """Both halves go into an API path and into a ConfigMap name built from
    them, and the service is scoped to the namespaces it was given. A string
    the cluster could never name, or a namespace this API does not serve, is
    not a campaign to go looking for (2026-09-14 audit) -- not refused after
    the read, but never read at all."""
    reader = Counting()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get(f"/api/v1/jobs/{namespace}/{name}").status_code == 404
    assert reader.asked == []


WARMUP_JOB = {
    "metadata": {
        "name": "warmup-demo-v1",
        "namespace": "htr-test",
        "uid": "uid-warmup",
        "labels": {
            "app": "htrflow-warmup",
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "spec": {"completions": 1},
    "status": {},
}


class _AnyJob(RecordingReader):
    """A namespace that also holds Jobs that are not campaigns."""

    def get_job(self, namespace: str, name: str) -> dict | None:
        if name == "warmup-demo-v1":
            return WARMUP_JOB
        if name == "someone-elses":
            return {**JOB, "metadata": {**JOB["metadata"], "labels": {}}}
        return super().get_job(namespace, name)


@pytest.mark.parametrize("name", ["warmup-demo-v1", "someone-elses"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_job_that_is_not_a_campaign_is_a_404_and_writes_nothing(
    name: str, method: str
):
    """Any Job name in a served namespace was answered as a campaign, and a
    status record written for it: `HEAD .../warmup-demo-v1` created
    `campaign-warmup-demo-v1-status` (2026-09-23 audit). Only a Job carrying
    the campaign labels the list selects by is one."""
    reader = _AnyJob()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    resp = client.request(method, f"/api/v1/jobs/htr-test/{name}")
    assert resp.status_code == 404
    assert reader.written == []


def test_a_campaign_the_cluster_could_carry_still_answers():
    reader = Counting()
    client = TestClient(create_app(reader, progress=FakeProgress()))
    assert client.get("/api/v1/jobs/htr-test/kyrk").status_code == 200
    assert reader.asked != []


def test_the_recording_fake_refuses_a_name_the_cluster_would():
    """The guard above is the point of this fake, so it is asserted here
    rather than only ever being true by accident."""
    reader = RecordingReader()
    with pytest.raises(AssertionError, match="DNS-1123"):
        reader.apply_configmap(
            {"metadata": {"name": "Campaign-Kyrk", "namespace": "htr-test"}}
        )


# --- the record's field ownership, against server-side apply itself -------

#: One volume done, one failed -- with a pod saying why, so a detail request
#: has a new reason to write.
FAILING_JOB = {**JOB, "status": {**JOB["status"], "active": 0, "failedIndexes": "1"}}
FAILED_POD = {
    "metadata": {
        "name": "kyrk-1-x",
        "creationTimestamp": "2026-01-01T00:01:00Z",
        "labels": {"batch.kubernetes.io/job-completion-index": "1"},
    },
    "status": {
        "containerStatuses": [
            {
                "name": "wrapper",
                "state": {"terminated": {"exitCode": 1, "message": "boom"}},
            }
        ]
    },
}
STATUS = "campaign-kyrk-status"
LABELS = {
    "htrflow.riksarkivet.se/managed-by": "converter",
    "htrflow.riksarkivet.se/campaign": "kyrk",
    "htrflow.riksarkivet.se/pipeline": "demo-v1",
    "htrflow.riksarkivet.se/kind": "status",
}
OLD_REASONS = '[{"id":"vol0","reason":"an older sentence"}]'


class SsaReader(FakeReader):
    """FakeReader whose status ConfigMap lives in an SSA-faithful store
    (conftest.py). ``stale`` stands in for a read made before another
    request's write: what this request saw, not what is there now."""

    def __init__(self, store, job: dict = FAILING_JOB) -> None:
        self.store, self.job = store, job
        self.pods = [FAILED_POD]
        self.stale: dict | None = None
        self.conflicts = 0

    def get_job(self, namespace: str, name: str) -> dict | None:
        return self.job if name == "kyrk" else None

    def list_jobs(self) -> list[dict]:
        return [self.job]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return self.pods

    def _status(self) -> dict | None:
        return self.stale if self.stale is not None else self.store.get(STATUS)

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return (
            self._status() if name == STATUS else super().get_configmap(namespace, name)
        )

    def list_configmaps(self) -> list[dict]:
        return [cm for cm in [self._status()] if cm is not None]

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = FIELD_MANAGER
    ) -> str | None:
        try:
            return self.store.apply(body, force=force, manager=manager)
        except ApiException as e:
            self.conflicts += 1
            raise ApplyConflict(body["metadata"]["name"]) from e


def _legacy(store) -> None:
    """A record as main left it: one manager, `htrflow-web`, owns it all --
    failedVolumes included."""
    store.apply(
        {
            "metadata": {"name": STATUS, "namespace": "htr-test", "labels": LABELS},
            "data": {
                "phase": "Running",
                "jobUid": "uid-kyrk",
                "failedVolumes": OLD_REASONS,
            },
        },
        manager=FIELD_MANAGER,
    )


def _reasons(store) -> dict:
    return projection._parse_failed(store.get(STATUS)["data"].get("failedVolumes", ""))


def test_an_upgraded_record_keeps_its_failures_and_hands_them_over(ssa):
    reader = SsaReader(ssa)
    _legacy(ssa)
    client_ = TestClient(create_app(reader, progress=FakeProgress()))
    client_.get("/api/v1/jobs")
    assert _reasons(ssa) == {"vol0": "an older sentence"}, "not released into deletion"
    assert ssa.owner_of(STATUS, "failedVolumes") == {FIELD_MANAGER}
    client_.get("/api/v1/jobs/htr-test/kyrk")
    assert _reasons(ssa) == {"vol1": "boom", "vol0": "an older sentence"}
    assert ssa.owner_of(STATUS, "failedVolumes") == {FAILURES_MANAGER}
    client_.get("/api/v1/jobs")
    assert set(_reasons(ssa)) == {"vol0", "vol1"}, "the list route leaves them be"


def test_a_list_write_racing_the_take_over_conflicts_and_then_heals(ssa):
    """It read the record before the failures manager took it over, so it
    still sends the old value as the summary's -- a 409, not an overwrite.
    The next poll reads the new owner and leaves the field alone."""
    reader = SsaReader(ssa)
    _legacy(ssa)
    client_ = TestClient(create_app(reader, progress=FakeProgress()))
    before = ssa.get(STATUS)
    client_.get("/api/v1/jobs/htr-test/kyrk")
    taken_over = _reasons(ssa)
    assert "vol1" in taken_over
    reader.stale = before
    assert client_.get("/api/v1/jobs").status_code == 200
    assert reader.conflicts == 1
    assert _reasons(ssa) == taken_over, "the stale value did not land"
    reader.stale = None
    client_.get("/api/v1/jobs")
    assert _reasons(ssa) == taken_over


def test_the_ssa_store_behaves_like_the_api_server(ssa):
    """The rules the tests above lean on, held to on their own."""
    cm = {"metadata": {"name": "x", "namespace": "n"}, "data": {"a": "1", "b": "1"}}
    uid = ssa.apply(cm, manager="one")
    with pytest.raises(ApiException):  # another manager's field, changed
        ssa.apply({**cm, "data": {"a": "2"}}, manager="two")
    ssa.apply({**cm, "data": {"b": "1"}}, manager="two")  # same value: shared
    ssa.apply({**cm, "data": {}}, manager="one")  # releases a, keeps shared b
    assert ssa.get("x")["data"] == {"b": "1"}
    ssa.apply({**cm, "data": {"b": "3"}}, force=True, manager="three")
    assert ssa.owner_of("x", "b") == {"three"}
    with pytest.raises(ApiException):  # a uid never creates
        ssa.apply(
            {"metadata": {"name": "y", "namespace": "n", "uid": uid}}, manager="one"
        )
    assert ssa.get("y") is None


def _old_run(store) -> None:
    """A record of the Job this name had before, as this API writes it: the
    summary under its manager, the failures, tied to that run, under theirs."""
    meta = {"name": STATUS, "namespace": "htr-test"}
    store.apply(
        {
            "metadata": {**meta, "labels": LABELS},
            "data": {"phase": "Failed", "jobUid": "uid-old"},
        },
        manager=FIELD_MANAGER,
    )
    store.apply(
        {
            "metadata": meta,
            "data": {"failedVolumes": OLD_REASONS, "failedVolumesJobUid": "uid-old"},
        },
        force=True,
        manager=FAILURES_MANAGER,
    )


def _reaped_reasons(store) -> dict:
    return projection._recorded_reasons(store.get(STATUS))


@pytest.mark.parametrize("writer", ["this API", "an older pod of it"])
def test_a_recreated_jobs_record_never_counts_the_last_runs_failures(ssa, writer):
    """The Job was recreated under the same name, and the summary moved to
    it -- written by this API, or, mid rolling update, by an older pod that
    knows nothing of the failures manager and leaves its field standing.
    The old run's failures are tied to the old run: never read as this
    run's, and never merged into them (2026-09-23 review)."""
    _old_run(ssa)
    reader = SsaReader(ssa)
    client_ = TestClient(create_app(reader, progress=FakeProgress()))
    if writer == "this API":
        client_.get("/api/v1/jobs")
    else:
        ssa.apply(
            {
                "metadata": {"name": STATUS, "namespace": "htr-test", "labels": LABELS},
                "data": {"phase": "Running", "jobUid": "uid-kyrk"},
            },
            manager=FIELD_MANAGER,
        )
    assert ssa.get(STATUS)["data"]["jobUid"] == "uid-kyrk"
    assert _reaped_reasons(ssa) == {}, "the old run's list is not this run's"
    client_.get("/api/v1/jobs/htr-test/kyrk")
    assert _reasons(ssa) == {"vol1": "boom"}
    assert ssa.get(STATUS)["data"]["failedVolumesJobUid"] == "uid-kyrk"
    assert _reaped_reasons(ssa) == {"vol1": "boom"}


def test_the_failures_write_never_creates_the_record(ssa):
    """`apply --prune` deleted the record between this request's read and
    its failures write. Sent as it was, that write created it again with no
    labels -- invisible to the list and to the next prune, for ever, and
    with no jobUid, so read as the next Job's (2026-09-23 review). It is
    held to the record it read, and a record that is gone stays gone."""
    reader = SsaReader(ssa)
    client_ = TestClient(create_app(reader, progress=FakeProgress()))
    client_.get("/api/v1/jobs/htr-test/kyrk")
    assert _reasons(ssa) == {"vol1": "boom"}
    reader.stale = ssa.get(STATUS)
    ssa.delete(STATUS)
    pod = copy.deepcopy(FAILED_POD)
    pod["status"]["containerStatuses"][0]["state"]["terminated"]["message"] = (
        "boom again"
    )
    reader.pods = [pod]
    assert client_.get("/api/v1/jobs/htr-test/kyrk").status_code == 200
    assert ssa.get(STATUS) is None
    assert reader.conflicts == 1


def test_a_first_record_gets_its_failures_in_the_same_request(ssa):
    """No record yet: the summary creates it, and the failures are held to
    the one it created."""
    reader = SsaReader(ssa)
    TestClient(create_app(reader, progress=FakeProgress())).get(
        "/api/v1/jobs/htr-test/kyrk"
    )
    assert _reasons(ssa) == {"vol1": "boom"}
    assert ssa.get(STATUS)["metadata"]["labels"] == LABELS
