"""The web front and the results proxy composed in one process, over the
production HTTP clients each builds for itself: login -> cookie -> the
``/api/v1`` gate -> progress read with the caller's cookie -> a result
file. Every httpx transport is answered by the proxy app; the store is
moto's."""

from __future__ import annotations

import json
from types import SimpleNamespace

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from htrflow_web.app import create_app
from htrflow_web.results import ResultsConfig, create_results_app
from htrflow_web.session import SessionCodec

KEY = bytes(range(32))
SITE = "https://site.example"
PROXY = "http://htrflow-results:8082/results"

JOB = {
    "metadata": {
        "name": "kyrk",
        "namespace": "htr-test",
        "creationTimestamp": "2026-09-01T00:00:00Z",
        "labels": {
            "app": "htrflow-batch",
            "htrflow.riksarkivet.se/managed-by": "converter",
            "htrflow.riksarkivet.se/campaign": "kyrk",
            "htrflow.riksarkivet.se/pipeline": "demo-v1",
        },
    },
    "spec": {
        "completions": 1,
        "template": {
            "spec": {
                "volumes": [
                    {"name": "campaign", "configMap": {"name": "campaign-kyrk"}}
                ]
            }
        },
    },
    "status": {"completedIndexes": "0", "conditions": []},
}
CAMPAIGN = {
    "metadata": {"name": "campaign-kyrk", "namespace": "htr-test"},
    "data": {"volumes.txt": "R1\thttps://iiif.example.org/R1/manifest\n"},
}


class OneCampaign:
    cfg = SimpleNamespace(
        results_url=f"{SITE}/results",
        internal_results_base=PROXY,
        results_proxy=PROXY,
        namespaces=("htr-test",),
    )

    def list_jobs(self) -> list[dict]:
        return [JOB]

    def list_warmups(self) -> list[dict]:
        return []

    def get_job(self, namespace: str, name: str) -> dict | None:
        return JOB if (namespace, name) == ("htr-test", "kyrk") else None

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return CAMPAIGN if name == "campaign-kyrk" else None

    def list_configmaps(self) -> list[dict]:
        return []

    def list_pipelines(self) -> list[dict]:
        return []

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        return []

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = "htrflow-web"
    ) -> str | None:
        return None


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="htr-results")
        s3.put_object(
            Bucket="htr-results",
            Key="htr-test/demo-v1/R1/iiif.json",
            Body=b'{"id": "R1"}',
            ContentType="application/json",
        )
        s3.put_object(
            Bucket="htr-results",
            Key="htr-test/demo-v1/R1/progress.json",
            Body=json.dumps({"pages_total": 3, "pages_done": 3}).encode(),
            ContentType="application/json",
        )
        cfg = ResultsConfig.from_env(
            {
                "HTRFLOW_RESULTS_NAMESPACE": "htr-test",
                "S3_BUCKET": "htr-results",
                "HTRFLOW_KEY_DERIVATION": "none",
            }
        )
        proxy_app = create_results_app(cfg, SessionCodec(KEY, hours=8))
        to_proxy = TestClient(proxy_app)
        to_proxy_async = httpx.ASGITransport(app=proxy_app)
        seen: list[httpx.Request] = []

        def answer(self, request):
            assert request.url.host == "htrflow-results"
            seen.append(request)
            return to_proxy._transport.handle_request(request)

        async def answer_async(self, request):
            assert request.url.host == "htrflow-results"
            seen.append(request)
            # Unread, as a real transport answers: the pass-through streams.
            return await to_proxy_async.handle_async_request(request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answer)
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", answer_async
        )
        web = create_app(OneCampaign(), static_dir="/nonexistent")
        yield TestClient(web, base_url=SITE), seen


def test_login_opens_the_api_and_the_files_and_logout_closes_them(site):
    c, seen = site
    assert c.get("/api/v1/jobs").status_code == 401
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 401

    r = c.post(
        "/results/_login",
        json={"username": "testing", "password": "testing"},
        headers={"Origin": SITE},
    )
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-htr_session=") and "secure" in cookie.lower()

    assert c.get("/api/v1/jobs").status_code == 200
    detail = c.get("/api/v1/jobs/htr-test/kyrk").json()
    # Read through the proxy with this caller's cookie, from the store.
    assert detail["volumes"][0]["progress"]["done"] == 3
    assert detail["pagesCoverage"] == {"counted": 1, "of": 1}
    f = c.get("/results/htr-test/demo-v1/R1/iiif.json")
    assert f.status_code == 200 and f.json() == {"id": "R1"}
    assert f.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert c.get("/results/other-ns/demo-v1/R1/iiif.json").status_code == 404
    # Every request the web front made went to the proxy.
    assert {req.url.host for req in seen} == {"htrflow-results"}

    out = c.post("/results/_logout", headers={"Origin": SITE})
    assert out.status_code == 204
    assert c.get("/results/htr-test/demo-v1/R1/iiif.json").status_code == 401


def test_a_login_from_another_origin_never_reaches_the_store(site):
    c, _ = site
    r = c.post(
        "/results/_login",
        json={"username": "testing", "password": "testing"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert "set-cookie" not in r.headers
