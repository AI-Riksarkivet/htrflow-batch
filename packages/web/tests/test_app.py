"""FastAPI wiring tests: fake reader, no cluster (docs: task-4-brief)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_web.app import create_app

JOB = {
    "metadata": {
        "name": "kyrk",
        "namespace": "htr-test",
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
    cfg = SimpleNamespace(public_results_base="https://results.example.org")

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

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return []


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(FakeReader()))


def test_healthz(client: TestClient):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_list_jobs_shape(client: TestClient):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "namespace": "htr-test",
            "name": "kyrk",
            "pipeline": "demo-v1",
            "phase": "Running",
            "counts": {"total": 2, "active": 1, "done": 1, "failed": 0},
            "suspended": False,
            "createdAt": "2026-01-01T00:00:00Z",
            "resultsBase": "https://results.example.org/htr-test/demo-v1",
            "warmup": {"phase": "missing"},
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


JOB2 = {**JOB, "metadata": {**JOB["metadata"], "name": "kyrk2"}}


class TwoCampaignsOneFailedWarmupReader(FailedWarmupReader):
    """Same namespace + pipeline label on both campaigns, so both match the
    one failed warm-up -- list_pods must be called once, not per campaign."""

    def __init__(self) -> None:
        self.list_pods_calls = 0

    def list_jobs(self) -> list[dict]:
        return [JOB, JOB2]

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        self.list_pods_calls += 1
        return super().list_pods(namespace, job_name)


def test_list_jobs_calls_list_pods_once_for_a_shared_failed_warmup():
    reader = TwoCampaignsOneFailedWarmupReader()
    client = TestClient(create_app(reader))
    body = client.get("/api/v1/jobs").json()
    assert len(body) == 2
    assert all(row["warmup"]["phase"] == "failed" for row in body)
    assert reader.list_pods_calls == 1


def test_job_detail_carries_the_warmup_field_too():
    """Task 28: the detail response inherits `warmup` from the same
    matching the list row does -- not just a `missing` default."""
    client = TestClient(create_app(FailedWarmupReader()))
    body = client.get("/api/v1/jobs/htr-test/kyrk").json()
    assert body["warmup"]["phase"] == "failed"
    assert body["warmup"]["reason"]["error"] == "unknown model class Yolo9"


def test_no_create_patch_delete_calls():
    """RBAC is read-only get/list/watch on jobs/pods/configmaps; the package
    must never call a mutating kubernetes-client method."""
    src = Path(__file__).parent.parent / "src" / "htrflow_web"
    offenders = []
    pattern = re.compile(r"\.(create_|patch_|delete_)\w*\(")
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []
