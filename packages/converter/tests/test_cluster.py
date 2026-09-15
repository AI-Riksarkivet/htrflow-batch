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

from htrflow_converter.cluster import (
    APPLY_PATCH,
    FIELD_MANAGER,
    REQUEST_TIMEOUT,
    Cluster,
    ClusterError,
)


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
                "timeout": kwargs.get("_request_timeout"),
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


def test_prune_delete_forbidden_is_one_sentence(cluster, monkeypatch):
    """``--prune`` needs ``delete`` where a plain apply does not; a Role that
    lacks it must fail in the same voice as everything else, not as a
    traceback out of the delete loop."""
    real = client.ApiClient.call_api

    def call_api(self, resource_path, method, *a, **kw):
        if method == "DELETE":
            raise ApiException(status=403, reason="Forbidden")
        return real(self, resource_path, method, *a, **kw)

    cluster.answer["GET"] = {"items": [{"metadata": {"name": "gone"}}]}
    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with pytest.raises(ClusterError) as exc:
        cluster.prune(set())
    assert str(exc.value).startswith(
        "not allowed to delete Job/gone in htr-batch: Forbidden"
    )


def test_the_other_api_error_sentences():
    """The two branches the wire tests do not reach: a missing Kueue, and
    the generic branch that carries the server's own message -- which is
    how an admission webhook's (Kyverno's) rejection arrives."""
    from htrflow_converter.cluster import _api_error

    missing = _api_error("list", "Workload", "", "htr-batch", ApiException(status=404))
    assert str(missing).startswith("Kueue is not installed in this cluster")
    # a 404 on the patch means the Workload went away, not that Kueue did
    gone = _api_error(
        "patch", "Workload", "wl-x", "htr-batch", ApiException(status=404)
    )
    assert str(gone) == "patch Workload/wl-x: 404 None"
    denied = ApiException(status=422, reason="Unprocessable Entity")
    denied.body = json.dumps(
        {"message": "admission webhook denied: image must be pinned"}
    )
    assert str(_api_error("apply", "Job", "kyrk", "htr-batch", denied)) == (
        "apply Job/kyrk: 422 Unprocessable Entity admission webhook denied: "
        "image must be pinned"
    )
    # Kyverno sends a paragraph, blank lines and all; every other problem
    # this package prints is one sentence, so this one is reflowed too.
    kyverno = ApiException(status=400, reason="Bad Request")
    kyverno.body = json.dumps(
        {
            "message": 'admission webhook "validate.kyverno.svc-fail" denied '
            "the request: \n\nresource Job/htr-batch/kyrk was blocked due to "
            "the following policies \n\nhtrflow-batch-images-pinned-htr-batch:"
            "\n  job-images-pinned: 'image must be pinned by digest: x:dev'\n"
        }
    )
    reflowed = str(_api_error("apply", "Job", "kyrk", "htr-batch", kyverno))
    assert "\n" not in reflowed
    assert reflowed.endswith(
        "job-images-pinned: 'image must be pinned by digest: x:dev'"
    )
    plain = ApiException(status=500, reason="Internal Server Error")
    plain.body = "<html>"
    assert str(_api_error("apply", "Job", "kyrk", "htr-batch", plain)) == (
        "apply Job/kyrk: 500 Internal Server Error"
    )


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


def _immutable_refusal(field: str = "spec.template") -> ApiException:
    """What the API server answers an apply that would change a Job's pod
    template: 422 Invalid, the whole rejected template quoted back in the
    message, and the field named in ``details.causes``."""
    e = ApiException(status=422, reason="Unprocessable Entity")
    e.body = json.dumps(
        {
            "kind": "Status",
            "reason": "Invalid",
            "message": (
                'Job.batch "htr-warmup-e2e-vd" is invalid: spec.template: '
                "Invalid value: core.PodTemplateSpec{ObjectMeta:v1.ObjectMeta"
                '{Name:"", GenerateName:"", …}}: field is immutable'
            ),
            "details": {
                "name": "htr-warmup-e2e-vd",
                "group": "batch",
                "kind": "Job",
                "causes": [
                    {
                        "reason": "FieldValueInvalid",
                        "message": "Invalid value: core.PodTemplateSpec{…}: "
                        "field is immutable",
                        "field": field,
                    }
                ],
            },
            "code": 422,
        }
    )
    return e


def test_a_refused_immutable_field_is_one_sentence_not_a_struct_dump():
    """The live failure this exists for printed the whole rejected pod
    template back as a page of Go struct dump. What a reader needs is the
    object, the field, and the rule."""
    from htrflow_converter.cluster import ImmutableField, _api_error

    e = _api_error(
        "apply", "Job", "htr-warmup-e2e-vd", "htr-batch", _immutable_refusal()
    )
    assert isinstance(e, ImmutableField)
    assert e.fields == ("spec.template",)
    assert str(e) == (
        "Job htr-warmup-e2e-vd: the pod template changed and a Job's pod "
        "template is immutable once the Job exists — a pipeline id is a "
        "permanent name for a recipe, so a changed recipe is a new pipeline "
        "file, and a Job that has to change is deleted and created again"
    )
    assert "PodTemplateSpec" not in str(e)


def test_another_immutable_field_is_named_as_itself():
    """Not only the pod template: moving a campaign between Kueue queues
    changes an immutable label, and that refusal has to name the label."""
    from htrflow_converter.cluster import ImmutableField, _api_error

    field = "metadata.labels[kueue.x-k8s.io/queue-name]"
    e = _api_error("apply", "Job", "kyrk", "htr-batch", _immutable_refusal(field))
    assert isinstance(e, ImmutableField)
    assert str(e).startswith(f"Job kyrk: {field} changed and is immutable")


def test_replace_job_deletes_in_the_background_then_creates_it_again(
    cluster, monkeypatch
):
    """The only way to give a Job a pod template it did not start with. The
    create has to follow the *deletion*, not the delete call: an apply
    against a name the API server still holds patches the old Job and is
    refused all over again."""
    real = client.ApiClient.call_api

    def call_api(self, resource_path, method, *a, **kw):
        if method == "GET":  # the Job is gone the moment it is deleted
            raise ApiException(status=404, reason="Not Found")
        return real(self, resource_path, method, *a, **kw)

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    cluster.replace_job(JOB)
    assert all(c["timeout"] == REQUEST_TIMEOUT for c in cluster.calls)
    assert [(c["method"], c["path"]) for c in cluster.calls] == [
        ("DELETE", "/apis/batch/v1/namespaces/htr-batch/jobs/kyrk"),
        ("PATCH", "/apis/batch/v1/namespaces/htr-batch/jobs/kyrk"),
    ]
    assert cluster.calls[0]["query"]["propagationPolicy"] == "Background"
    assert cluster.calls[1]["content_type"] == APPLY_PATCH


def test_a_job_that_will_not_go_away_is_a_sentence(cluster, monkeypatch):
    """Waiting forever on a deletion that is stuck would hang the apply."""
    monkeypatch.setattr("htrflow_converter.cluster.time.sleep", lambda _: None)
    monkeypatch.setattr("htrflow_converter.cluster.DELETE_WAIT", 2)
    cluster.answer["GET"] = {"metadata": {"name": "kyrk"}}  # still there
    with pytest.raises(ClusterError) as exc:
        cluster.replace_job(JOB)
    assert "still there 2s later" in str(exc.value)


def test_a_server_message_is_capped_before_it_is_repeated():
    """A 422 whose `details.causes` are empty still carries the whole
    rejected value in `message`, and for a pod template that is thousands of
    characters of Go struct. This line goes to a terminal."""
    from htrflow_converter.cluster import MAX_MESSAGE, _api_error

    huge = ApiException(status=422, reason="Unprocessable Entity")
    huge.body = json.dumps({"message": "spec.template: " + "core.Pod{} " * 500})
    line = str(_api_error("apply", "Job", "kyrk", "htr-batch", huge))
    assert len(line) < MAX_MESSAGE + 100
    assert line.startswith("apply Job/kyrk: 422 Unprocessable Entity spec.template: ")
    assert line.endswith("…")


def test_every_request_carries_a_connect_and_read_timeout(cluster):
    """Without one, a half-open connection to the API server hangs the apply
    for ever: nothing above this has a deadline of its own, and an apply that
    never returns is a campaigns repo whose CI job never returns either."""
    cluster.answer["GET"] = {"items": [{"metadata": {"name": "gone"}}]}
    cluster.apply(JOB)
    cluster.get("Job", "kyrk")
    cluster.prune(set())
    cluster.sync_pause({"metadata": {"name": "k", "uid": "u9"}}, True, 0)
    verbs = {c["method"] for c in cluster.calls}
    assert verbs == {"PATCH", "GET", "DELETE"}, verbs
    assert {c["timeout"] for c in cluster.calls} == {REQUEST_TIMEOUT}


def test_a_refused_campaign_job_is_given_a_campaigns_way_out():
    """The advice was written for a pipeline file and printed for every Job
    alike: a campaign Job carries no recipe of its own, so "a changed recipe
    is a new pipeline file" sent its reader to edit a file that is not the
    one in front of them. A live campaign's Job simply cannot change."""
    from htrflow_converter.cluster import ImmutableField, _api_error

    e = _api_error("apply", "Job", "kyrk", "htr-batch", _immutable_refusal())
    assert isinstance(e, ImmutableField)
    assert str(e) == (
        "Job kyrk: the pod template changed and a Job's pod template is "
        "immutable once the Job exists — a live campaign's Job cannot change, "
        "so finish or remove the campaign, then apply"
    )


def _flaky(monkeypatch, status: int, failures: int) -> list[str]:
    """Make the first ``failures`` requests fail with ``status``."""
    real = client.ApiClient.call_api
    attempts: list[str] = []

    def call_api(self, resource_path, method, *a, **kw):
        attempts.append(method)
        if len(attempts) <= failures:
            raise ApiException(status=status, reason="flaky")
        return real(self, resource_path, method, *a, **kw)

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    return attempts


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr("htrflow_converter.cluster.time.sleep", waits.append)
    return waits


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_busy_or_restarting_api_server_is_retried(
    cluster, monkeypatch, slept, status
):
    """An apply is a long sequence of requests against a control plane that
    is upgraded, rate-limited and load-balanced. One 503 from a restarting
    apiserver used to leave a campaign unapplied and the operator re-running
    the whole command."""
    attempts = _flaky(monkeypatch, status, 2)
    cluster.apply(JOB)
    assert attempts == ["PATCH"] * 3
    assert slept == [1, 2]


def test_a_retry_is_bounded_and_then_says_so(cluster, monkeypatch, slept):
    """A server that keeps refusing is not waited on for ever: the sentence
    the last refusal carries is the one the apply reports."""
    attempts = _flaky(monkeypatch, 503, 99)
    with pytest.raises(ClusterError) as exc:
        cluster.apply(JOB)
    assert attempts == ["PATCH"] * 4
    assert slept == [1, 2, 4]
    assert str(exc.value) == "apply Job/kyrk: 503 flaky"


def test_a_refusal_the_server_meant_is_not_retried(cluster, monkeypatch, slept):
    """409, 403, 422: answers about this request, not about the server's
    moment. Retrying them wastes a minute and changes nothing."""
    attempts = _flaky(monkeypatch, 409, 99)
    with pytest.raises(ClusterError):
        cluster.apply(JOB)
    assert attempts == ["PATCH"]
    assert slept == []


def test_a_delete_retried_past_a_5xx_takes_a_404_as_done(slept):
    """The retry itself makes this 404: the first attempt reached the API
    server and only the answer was lost, so the object is gone because of
    this call, not missing before it. A first-attempt 404 still stands --
    that one is an object the caller was wrong about."""
    from htrflow_converter.cluster import _retrying

    answers = [ApiException(status=503, reason="flaky"), ApiException(status=404)]

    def delete():
        raise answers.pop(0)

    assert _retrying(delete, gone_is_done=True) is None
    assert slept == [1]

    with pytest.raises(ApiException) as exc:
        _retrying(
            lambda: (_ for _ in ()).throw(ApiException(status=404)), gone_is_done=True
        )
    assert exc.value.status == 404
