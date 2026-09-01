"""FastAPI wiring tests: fake reader, no cluster (docs: task-4-brief)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from htrflow_api.app import create_app

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


class FakeReader:
    cfg = SimpleNamespace(
        public_results_base="https://results.example.org", legacy_layout=False
    )

    def list_jobs(self) -> list[dict]:
        return [JOB]

    def get_job(self, namespace: str, name: str) -> dict | None:
        if (
            namespace == JOB["metadata"]["namespace"]
            and name == JOB["metadata"]["name"]
        ):
            return JOB
        return None

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return CONFIGMAP

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


def test_no_create_patch_delete_calls():
    """RBAC is read-only get/list/watch on jobs/pods/configmaps; the package
    must never call a mutating kubernetes-client method."""
    src = Path(__file__).parent.parent / "src" / "htrflow_api"
    offenders = []
    pattern = re.compile(r"\.(create_|patch_|delete_)\w*\(")
    for path in src.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []
