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
import re
from pathlib import Path

import pytest
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import (
    APPLY_PATCH,
    FIELD_MANAGER,
    REQUEST_TIMEOUT,
    SUSPEND_HOLDER,
    Cluster,
    ClusterError,
    Unreachable,
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


def _suspended(*managers: str) -> dict:
    fields = {"f:spec": {"f:suspend": {}}}
    return {
        "metadata": {
            "name": "kyrk",
            "managedFields": [{"manager": m, "fieldsV1": fields} for m in managers],
        },
        "spec": {"suspend": True},
    }


def test_holding_suspend_is_an_unforced_apply_of_that_field_alone(cluster):
    """The hand-over server-side apply prescribes: the same value, under a
    manager of its own, never forced -- so it can never take the field from
    Kueue, only share it with the apply's own manager."""
    cluster.hold_suspend(_suspended(FIELD_MANAGER))
    (call,) = cluster.calls
    assert call["path"] == "/apis/batch/v1/namespaces/htr-batch/jobs/kyrk"
    assert call["content_type"] == APPLY_PATCH
    assert call["query"]["fieldManager"] == SUSPEND_HOLDER
    assert call["query"]["force"] is False
    assert call["body"]["spec"] == {"suspend": True}


def test_suspend_is_held_only_when_the_apply_alone_owns_it(cluster):
    cluster.hold_suspend(_suspended(FIELD_MANAGER, "kueue"))  # Kueue holds it
    cluster.hold_suspend({**_suspended(FIELD_MANAGER), "spec": {"suspend": False}})
    assert cluster.calls == []


def test_a_conflict_while_holding_suspend_means_kueue_has_it(cluster, monkeypatch):
    def conflict(*args, **kwargs):
        raise ApiException(status=409, reason="Conflict")

    monkeypatch.setattr(client.ApiClient, "call_api", conflict)
    cluster.hold_suspend(_suspended(FIELD_MANAGER))  # no exception


def test_a_dry_run_apply_is_the_same_patch_with_dry_run_all(cluster):
    """What lets apply ask about a campaign's Job before its ConfigMap is
    sent (3084): the API server, admission webhooks included, answers as it
    would for the real apply and stores nothing."""
    cluster.apply(JOB, dry_run=True)
    cluster.apply(JOB)
    dry, real = cluster.calls
    assert dry["query"]["dryRun"] == "All"
    assert "dryRun" not in real["query"]
    assert dry["content_type"] == real["content_type"] == APPLY_PATCH


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


def test_prune_delete_forbidden_is_one_sentence_and_the_prune_goes_on(
    cluster, monkeypatch
):
    """``--prune`` needs ``delete`` where a plain apply does not; a Role that
    lacks it must fail in the same voice as everything else, not as a
    traceback out of the delete loop -- and one object it may not delete is
    that object's problem, not the end of the prune (3090)."""
    real = client.ApiClient.call_api

    def call_api(self, resource_path, method, *a, **kw):
        if method == "DELETE":
            raise ApiException(status=403, reason="Forbidden")
        return real(self, resource_path, method, *a, **kw)

    cluster.answer["GET"] = {"items": [{"metadata": {"name": "gone"}}]}
    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    problems = cluster.prune(set())
    assert [what for what, _ in problems] == ["Job/gone", "ConfigMap/gone"]
    assert problems[0][1].startswith(
        "not allowed to delete Job/gone in htr-batch: Forbidden"
    )


def test_a_prune_that_may_not_list_one_kind_still_prunes_the_other(
    cluster, monkeypatch
):
    real = client.ApiClient.call_api

    def call_api(self, resource_path, method, *a, **kw):
        if method == "GET" and "/jobs" in resource_path:
            raise ApiException(status=403, reason="Forbidden")
        return real(self, resource_path, method, *a, **kw)

    cluster.answer["GET"] = {"items": [{"metadata": {"name": "gone"}}]}
    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    problems = cluster.prune(set())
    ((what, sentence),) = problems
    assert what == "Job"
    assert sentence.startswith("not allowed to list Job in htr-batch")
    assert [c["method"] for c in cluster.calls] == ["GET", "DELETE"]


def test_the_other_api_error_sentences():
    """The two branches the wire tests do not reach: a missing Kueue, and
    the generic branch that carries the server's own message -- which is
    how an admission webhook's (Kyverno's) rejection arrives."""
    from htrflow_converter.cluster import _api_error

    missing = _api_error("list", "Workload", "", "htr-batch", ApiException(status=404))
    assert str(missing).startswith("Kueue is not installed in this cluster")
    assert "kueue.x-k8s.io/v1beta2" in str(missing), "a version not served 404s too"
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


def test_unreachable_api_server_is_one_sentence(cluster, monkeypatch, slept):
    """``MaxRetryError`` is not wrapped in ``ApiException`` -- it is what a
    bad or unreachable ``KUBECONFIG`` server actually raises."""

    def call_api(self, *a, **kw):
        raise MaxRetryError(pool=None, url="/", reason=OSError("Connection refused"))

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with pytest.raises(Unreachable) as exc:
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
        "/apis/kueue.x-k8s.io/v1beta2/namespaces/htr-batch/workloads"
    )
    assert call["query"]["labelSelector"] == "kueue.x-k8s.io/job-uid=u9"


def test_the_pause_sync_speaks_the_kueue_version_the_chart_installs_objects_in():
    """The chart creates the queue in one Kueue API version and the pause
    sync patched Workloads in another, older one. Kueue serves a deprecated
    version only until it drops it, and on that day the list 404s -- read as
    "Kueue is not installed" -- and a campaign git says is paused keeps
    running. One version, and a rename on either side fails here."""
    template = Path(__file__).parents[3] / "charts/htrflow-batch/templates/kueue.yaml"
    versions = set(
        re.findall(r"^apiVersion:\s*(kueue\.x-k8s\.io/\S+)", template.read_text(), re.M)
    )
    assert versions == {"/".join(cluster_mod._KUEUE)}


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


def test_a_delete_takes_a_404_as_done_on_every_attempt(slept):
    """A delete wants the object gone, and a 404 says it is. On a retry the
    first attempt reached the API server and only its answer was lost; on
    the FIRST attempt the TTL controller reaped the Job between the prune's
    list and its delete -- which used to raise, and end the apply before its
    pause sync (3090)."""
    from htrflow_converter.cluster import _retrying

    answers = [ApiException(status=503, reason="flaky"), ApiException(status=404)]

    def delete():
        raise answers.pop(0)

    assert _retrying(delete, gone_is_done=True) is None
    assert slept == [1]

    def reaped():
        raise ApiException(status=404)

    assert _retrying(reaped, gone_is_done=True) is None
    with pytest.raises(ApiException):
        _retrying(reaped)


def test_a_lost_connection_is_retried_like_a_busy_server(cluster, monkeypatch, slept):
    """A read timeout or a refused connection is not the API server's answer
    about the request -- there is no answer at all, and a server-side apply
    that did land is safe to send again. It used to be one attempt and then
    a "refused" object (3091)."""
    real = client.ApiClient.call_api
    attempts: list[str] = []

    def call_api(self, resource_path, method, *a, **kw):
        attempts.append(method)
        if len(attempts) <= 2:
            raise ReadTimeoutError(pool=None, url="/", message="Read timed out.")
        return real(self, resource_path, method, *a, **kw)

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    cluster.apply(JOB)
    assert attempts == ["PATCH"] * 3
    assert slept == [1, 2]


def test_a_server_that_stays_gone_is_unreachable_not_refused(
    cluster, monkeypatch, slept
):
    """Bounded like every other retry, and then a class of its own: the
    caller stops the apply on it rather than filing it as one object the
    API server refused (3091)."""

    def call_api(self, *a, **kw):
        raise MaxRetryError(pool=None, url="/", reason=OSError("Connection refused"))

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with pytest.raises(Unreachable):
        cluster.get("Job", "kyrk")
    assert slept == [1, 2, 4]


def test_the_lease_is_a_coordination_lease_created_then_released(cluster, monkeypatch):
    """One apply at a time: a GET that finds no Lease, a POST that creates
    it holding this process, and a PUT on the way out that lets it go --
    carrying the resourceVersion the POST answered, so a Lease taken over in
    between is a 409, not a second holder."""
    sent: list[tuple[str, str, dict | None]] = []

    def call_api(self, resource_path, method, path_params=None, *args, **kwargs):
        path = resource_path.format(**(path_params or {}))
        sent.append((method, path, kwargs.get("body")))
        if method == "GET":
            raise ApiException(status=404, reason="Not Found")
        body = json.loads(json.dumps(kwargs["body"]))
        body["metadata"]["resourceVersion"] = "41"
        return _Response(body)

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    with cluster.lease():
        pass
    base = "/apis/coordination.k8s.io/v1/namespaces/htr-batch/leases"
    (get, _, _), (post, post_path, created), (put, put_path, released) = sent
    assert (get, post, post_path) == ("GET", "POST", base)
    assert created["spec"]["holderIdentity"]
    assert created["spec"]["leaseDurationSeconds"] == cluster_mod.LEASE_SECONDS
    assert (put, put_path) == ("PUT", f"{base}/{cluster_mod.LEASE}")
    assert released["spec"]["holderIdentity"] is None
    assert released["metadata"]["resourceVersion"] == "41"
