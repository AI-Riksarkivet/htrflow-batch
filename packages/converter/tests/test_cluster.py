"""``Cluster`` against the real ``kubernetes`` client, with only the socket
mocked.

``test_apply.py`` drives the command through a fake ``Cluster`` -- fast, and
about the *decisions* apply makes. This file is the other half: the real
generated client, with ``ApiClient.call_api`` intercepted, so the HTTP
request it would put on the wire is asserted. What matters there is exactly
what ``kubectl apply --server-side`` puts on the wire too:

    PATCH …/jobs/<name>?fieldManager=htrflow-campaigns&force=True
    Content-Type: application/apply-patch+yaml

Get the content type wrong and the API server treats the manifest as a
strategic-merge patch -- which silently *merges* lists instead of replacing
them, and never removes a field this tool stopped rendering.
"""

import json

import pytest
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import MaxRetryError

from htrflow_converter.cluster import APPLY_PATCH, FIELD_MANAGER, Cluster, ClusterError


class _Response:
    """What ``call_api(_preload_content=False)`` hands back: raw bytes."""

    def __init__(self, body: dict) -> None:
        self.data = json.dumps(body).encode()


@pytest.fixture
def cluster(monkeypatch):
    """A real ``Cluster`` whose every request is recorded, not sent."""
    monkeypatch.setattr(
        config,
        "load_incluster_config",
        lambda: (_ for _ in ()).throw(config.ConfigException("not in a pod")),
    )
    monkeypatch.setattr(config, "load_kube_config", lambda: None)
    calls: list[dict] = []
    answer: dict = {}

    def call_api(
        self, resource_path, method, path_params=None, query_params=None,
        header_params=None, **kwargs,
    ):  # fmt: skip
        calls.append(
            {
                "path": resource_path.format(**(path_params or {})),
                "method": method,
                "query": dict(query_params or []),
                "content_type": (header_params or {}).get("Content-Type"),
                "body": kwargs.get("body"),
            }
        )
        body = answer.get(method, {})
        return _Response(body) if kwargs.get("_preload_content") is False else body

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    c = Cluster("htr-batch")
    c.calls, c.answer = calls, answer  # type: ignore[attr-defined]
    return c


JOB = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {"name": "kyrk", "namespace": "htr-batch"},
    "spec": {"suspend": False},
}
CM = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "campaign-kyrk", "namespace": "htr-batch"},
    "data": {"volumes.txt": "R1"},
}


@pytest.mark.parametrize(
    ("obj", "path"),
    [
        (JOB, "/apis/batch/v1/namespaces/htr-batch/jobs/kyrk"),
        (CM, "/api/v1/namespaces/htr-batch/configmaps/campaign-kyrk"),
    ],
)
def test_apply_is_a_server_side_apply_patch(cluster, obj, path):
    cluster.apply(obj)
    (call,) = cluster.calls
    assert (call["method"], call["path"]) == ("PATCH", path)
    assert call["content_type"] == APPLY_PATCH
    assert call["query"]["fieldManager"] == FIELD_MANAGER
    assert call["query"]["force"] is True
    assert call["body"] is obj, "the manifest itself is the patch"


def test_an_unknown_kind_is_a_sentence_not_a_keyerror(cluster):
    """The day a ``Service`` (or anything else this tool does not render)
    shows up in ``manifests/``, ``apply`` must not blow up with a bare
    ``KeyError: 'Service'``."""
    obj = {"kind": "Service", "metadata": {"name": "x", "namespace": "htr-batch"}}
    with pytest.raises(ClusterError) as exc:
        cluster.apply(obj)
    assert str(exc.value) == (
        "Service/x: htrflow-campaigns apply only handles Job, ConfigMap"
    )


def test_apply_returns_what_the_server_stored(cluster):
    """The uid in the response is the only link to the Kueue Workload."""
    cluster.answer["PATCH"] = {"metadata": {"name": "kyrk", "uid": "uid-1"}}
    assert cluster.apply(JOB)["metadata"]["uid"] == "uid-1"


def test_prune_lists_by_the_renderers_label_and_deletes_jobs_in_background(cluster):
    cluster.answer["GET"] = {"items": [{"metadata": {"name": "gone"}}]}
    cluster.prune(set())
    lists = [c for c in cluster.calls if c["method"] == "GET"]
    deletes = [c for c in cluster.calls if c["method"] == "DELETE"]
    assert {c["query"]["labelSelector"] for c in lists} == {
        "htrflow.riksarkivet.se/managed-by=converter"
    }
    assert [c["path"] for c in deletes] == [
        "/apis/batch/v1/namespaces/htr-batch/jobs/gone",
        "/api/v1/namespaces/htr-batch/configmaps/gone",
    ]
    assert deletes[0]["query"]["propagationPolicy"] == "Background"


def test_forbidden_is_one_sentence(cluster, monkeypatch):
    """``ApiException`` 401/403 names the verb, the object and the fix --
    turning on the chart's RBAC -- rather than a raw HTTP status."""

    def call_api(self, *a, **kw):
        raise ApiException(status=403, reason="Forbidden")

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with pytest.raises(ClusterError) as exc:
        cluster.apply(JOB)
    assert str(exc.value) == (
        "not allowed to apply Job/kyrk in htr-batch: Forbidden — the "
        "htrflow-batch chart renders the needed ServiceAccount behind "
        "apply.rbac.enabled"
    )


def test_unreachable_api_server_is_one_sentence(cluster, monkeypatch):
    """``MaxRetryError`` is not wrapped in ``ApiException`` -- it is what a
    bad or unreachable ``KUBECONFIG`` server actually raises."""

    def call_api(self, *a, **kw):
        raise MaxRetryError(pool=None, url="/", reason=OSError("Connection refused"))

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with pytest.raises(ClusterError) as exc:
        cluster.apply(JOB)
    message = str(exc.value)
    assert message.startswith("cannot reach the Kubernetes API server at ")
    assert "Connection refused" in message
    assert "\n" not in message


def test_no_incluster_and_no_kubeconfig_is_one_sentence(monkeypatch):
    """Neither loader working -- no pod token, no usable kubeconfig -- must
    not surface as a bare ``ConfigException`` from outside any ``try``."""
    monkeypatch.setattr(
        config,
        "load_incluster_config",
        lambda: (_ for _ in ()).throw(config.ConfigException("not in a pod")),
    )
    monkeypatch.setattr(
        config,
        "load_kube_config",
        lambda: (_ for _ in ()).throw(config.ConfigException("no current context")),
    )
    with pytest.raises(ClusterError) as exc:
        Cluster("htr-batch")
    assert str(exc.value) == (
        "no Kubernetes credentials: not running in a pod and no usable "
        "kubeconfig (set KUBECONFIG, or run kubectl config use-context)"
    )


def test_pause_sync_failure_goes_to_stderr(cluster, capsys):
    """This is the one line that accompanies a non-zero exit -- it must not
    be mixed into the progress output on stdout."""
    cluster.answer["GET"] = {"items": []}
    rc = cluster.sync_pause({"metadata": {"name": "k", "uid": "u9"}}, True, 0)
    assert rc == 1
    captured = capsys.readouterr()
    assert "paused in git" in captured.err
    assert "paused in git" not in captured.out


def test_the_workload_is_found_by_the_jobs_uid(cluster):
    cluster.answer["GET"] = {"items": []}
    assert cluster.sync_pause({"metadata": {"name": "k", "uid": "u9"}}, False, 0) == 0
    (call,) = cluster.calls
    assert call["path"] == (
        "/apis/kueue.x-k8s.io/v1beta1/namespaces/htr-batch/workloads"
    )
    assert call["query"]["labelSelector"] == "kueue.x-k8s.io/job-uid=u9"
