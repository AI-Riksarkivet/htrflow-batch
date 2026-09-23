"""``Config.from_env`` and ``Reader``: the web front's env contract, and the
requests the adapter actually puts on the wire.

``test_app.py`` drives the routes through a fake reader -- fast, and about
what the API answers. This file is the other half: the real generated
``kubernetes`` client with ``ApiClient.call_api`` intercepted, so the HTTP
request is asserted (the same intercept
``packages/converter/tests/test_cluster.py`` uses). Get the apply's content
type wrong and the API server treats the record as a strategic-merge patch;
get the 404 handling wrong and a reaped campaign 500s instead of falling
back to its record.
"""

from __future__ import annotations

import json

import pytest
from kubernetes import client, config
from urllib3.exceptions import MaxRetryError, ReadTimeoutError

from htrflow_web import kube, projection
from htrflow_web.kube import (
    CAMPAIGN_CONFIGMAPS,
    FIELD_MANAGER,
    LABEL_SELECTOR,
    PARTIAL_METADATA,
    ApplyConflict,
    ClusterUnavailable,
    Config,
    Reader,
)


def test_missing_results_base_raises():
    with pytest.raises(RuntimeError, match="HTRFLOW_PUBLIC_RESULTS_BASE is required"):
        Config.from_env({})


def test_site_only_does_not_require_a_results_base():
    """The compose stack has the site and no bucket; the base it would build
    result URLs from is not a setting it has to carry."""
    cfg = Config.from_env({"HTRFLOW_WEB_SITE_ONLY": "1"})
    assert cfg.site_only is True
    assert cfg.public_results_base == ""


def test_site_only_zero_still_counts_as_true():
    """``from_env`` treats any non-empty value as true -- including the
    string "0" -- so a results base is still not required."""
    assert Config.from_env({"HTRFLOW_WEB_SITE_ONLY": "0"}).site_only is True


def test_results_base_trailing_slash_is_stripped():
    cfg = Config.from_env({"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x/results/"})
    assert cfg.public_results_base == "http://x/results"


def test_namespaces_splits_on_comma_and_strips():
    cfg = Config.from_env(
        {"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x", "HTRFLOW_NAMESPACES": "a, b"}
    )
    assert cfg.namespaces == ("a", "b")


def test_static_dir_passes_through():
    cfg = Config.from_env(
        {"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x", "HTRFLOW_WEB_STATIC": "/site"}
    )
    assert cfg.static_dir == "/site"


def test_batch_version_defaults_to_the_dockerfile_default():
    """The image bakes HTRFLOW_BATCH_VERSION; a process started without one
    (a laptop, a source checkout) says the same thing the image would."""
    cfg = Config.from_env({"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x"})
    assert cfg.batch_version == "dev"


def test_batch_version_is_the_deployed_tag():
    cfg = Config.from_env(
        {"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x", "HTRFLOW_BATCH_VERSION": "v0.2.0"}
    )
    assert cfg.batch_version == "v0.2.0"


def test_internal_results_base_defaults_to_the_public_one():
    """The API pod's own ProgressReader must reach the bucket even when
    nobody set HTRFLOW_INTERNAL_RESULTS_BASE -- true on real AWS, where the
    same URL really does work from inside the cluster."""
    cfg = Config.from_env({"HTRFLOW_PUBLIC_RESULTS_BASE": "http://x/results"})
    assert cfg.internal_results_base == "http://x/results"


def test_internal_results_base_can_differ_from_the_public_one():
    """The PoC: publicResultsBase is a localhost URL reached through an SSH
    forward, which the pod itself cannot resolve to anything but itself."""
    cfg = Config.from_env(
        {
            "HTRFLOW_PUBLIC_RESULTS_BASE": "http://localhost:30900/htr-results",
            "HTRFLOW_INTERNAL_RESULTS_BASE": (
                "http://rustfs.htr-batch.svc.cluster.local:9000/htr-results/"
            ),
        }
    )
    assert (
        cfg.internal_results_base
        == "http://rustfs.htr-batch.svc.cluster.local:9000/htr-results"
    )


# --- Reader: what the adapter puts on the wire ---------------------------


class _Response:
    """What ``call_api(_preload_content=False)`` hands back: raw bytes."""

    def __init__(self, body: dict) -> None:
        self.data = json.dumps(body).encode()


def _api_error(status: int) -> client.ApiException:
    return client.ApiException(status=status, reason="from the fixture")


@pytest.fixture
def reader(monkeypatch) -> Reader:
    """A real ``Reader`` whose every request is recorded, not sent.

    ``reader.answer`` maps an HTTP method to what the API server says: a
    dict is decoded as the body, an exception is raised, and a list is a
    queue of either (so a retry can be given a different answer)."""
    monkeypatch.setattr(
        config,
        "load_incluster_config",
        lambda: (_ for _ in ()).throw(config.ConfigException("not in a pod")),
    )
    monkeypatch.setattr(config, "load_kube_config", lambda: None)
    calls: list[dict] = []
    answer: dict[str, object] = {}

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
                "accept": (header_params or {}).get("Accept"),
                "body": kwargs.get("body"),
                "timeout": kwargs.get("_request_timeout"),
            }
        )
        reply = answer.get(method, {})
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    monkeypatch.setattr(client.ApiClient, "call_api", call_api)
    r = Reader(
        Config.from_env(
            {
                "HTRFLOW_PUBLIC_RESULTS_BASE": "http://x",
                "HTRFLOW_NAMESPACES": "htr-a,htr-b",
            }
        )
    )
    r.calls, r.answer = calls, answer  # type: ignore[attr-defined]
    return r


def test_list_jobs_asks_every_namespace_with_the_campaign_selector(reader: Reader):
    """One list per configured namespace -- the warm-up Jobs carry
    `managed-by=converter` too, so the selector is what keeps them out."""
    reader.answer["GET"] = {"items": [{"metadata": {"name": "kyrk"}}]}
    assert len(reader.list_jobs()) == 2
    assert [c["path"] for c in reader.calls] == [
        "/apis/batch/v1/namespaces/htr-a/jobs",
        "/apis/batch/v1/namespaces/htr-b/jobs",
    ]
    assert {c["query"]["labelSelector"] for c in reader.calls} == {LABEL_SELECTOR}


def test_list_warmups_asks_for_the_warmup_jobs_instead(reader: Reader):
    reader.answer["GET"] = {"items": []}
    reader.list_warmups()
    selectors = {c["query"]["labelSelector"] for c in reader.calls}
    assert selectors == {
        "app=htrflow-warmup,htrflow.riksarkivet.se/managed-by=converter"
    }


def test_the_campaign_record_is_listed_without_its_volumes(reader: Reader):
    """`volumes.txt` is the whole campaign -- one line per volume, megabytes
    for a real backfill -- and the list route reads nothing but the record's
    labels and dates off it. Asking for PartialObjectMetadata keeps those
    bytes off the wire on every poll of every open status page."""
    reader.answer["GET"] = {"items": []}
    reader.list_configmaps()
    records = [c for c in reader.calls if "!=status" in c["query"]["labelSelector"]]
    assert len(records) == len(reader.cfg.namespaces)
    for call in records:
        assert call["path"].endswith("/configmaps")
        assert call["accept"] == PARTIAL_METADATA
        assert call["query"]["labelSelector"].startswith(CAMPAIGN_CONFIGMAPS)


def test_the_status_record_is_listed_with_its_data(reader: Reader):
    """The status ConfigMap IS the campaign once its Job is gone: a handful
    of short fields, and the page cannot draw the row without them."""
    reader.answer["GET"] = {"items": []}
    reader.list_configmaps()
    statuses = [
        c for c in reader.calls if "!=status" not in c["query"]["labelSelector"]
    ]
    assert len(statuses) == len(reader.cfg.namespaces)
    assert all(c["accept"] != PARTIAL_METADATA for c in statuses)


def test_list_pods_asks_for_one_jobs_pods(reader: Reader):
    reader.answer["GET"] = {"items": []}
    reader.list_pods("htr-a", "kyrk")
    assert reader.calls[0]["path"] == "/api/v1/namespaces/htr-a/pods"
    selector = reader.calls[0]["query"]["labelSelector"]
    assert selector == "batch.kubernetes.io/job-name=kyrk"


def test_list_pods_leaves_the_succeeded_ones_on_the_server(reader: Reader):
    """A campaign keeps every pod of every index until its Job goes -- up
    to four per index with three retries -- and a succeeded one belongs to
    a done index, which the Job's own status already says. Asked for, a
    campaign of thousands was tens of MB per poll in a 256Mi pod (2026-09-23
    audit)."""
    reader.answer["GET"] = {"items": []}
    reader.list_pods("htr-a", "kyrk")
    query = reader.calls[0]["query"]
    assert query["fieldSelector"] == "status.phase!=Succeeded"
    assert query["limit"] == kube.POD_PAGE


def test_list_pods_follows_the_continue_token_to_the_last_page(reader: Reader):
    page = {"metadata": {"name": "p"}, "status": {}}
    reader.answer["GET"] = [
        {"metadata": {"continue": "t1"}, "items": [page]},
        {"metadata": {"continue": "t2"}, "items": [page]},
        {"metadata": {}, "items": [page]},
    ]
    assert len(reader.list_pods("htr-a", "kyrk")) == 3
    assert [c["query"].get("continue") for c in reader.calls] == [None, "t1", "t2"]


def test_list_pods_keeps_only_what_the_projection_reads(reader: Reader):
    pod = {
        "metadata": {"name": "p", "managedFields": [{"manager": "kubelet"}]},
        "spec": {"containers": [{"name": "wrapper"}]},
        "status": {"phase": "Failed", "reason": "Evicted"},
    }
    reader.answer["GET"] = {"items": [pod]}
    assert reader.list_pods("htr-a", "kyrk") == [projection.pod_fields(pod)]


def test_a_missing_object_is_none_not_an_error(reader: Reader):
    """A campaign whose Job the TTL reaped is a 404, and the detail route
    falls back to its record on exactly this ``None`` (B76)."""
    reader.answer["GET"] = _api_error(404)
    assert reader.get_job("htr-a", "gamla") is None
    assert reader.get_configmap("htr-a", "campaign-gamla") is None


@pytest.mark.parametrize("status", [403, 429, 500])
def test_a_refused_read_is_one_exception_the_api_can_answer(
    reader: Reader, status: int
):
    reader.answer["GET"] = _api_error(status)
    with pytest.raises(ClusterUnavailable):
        reader.list_jobs()


def test_a_connection_that_never_answers_is_the_same_exception(reader: Reader):
    """urllib3 raises for a refused connection or a timeout long before
    there is an HTTP status to look at."""
    reader.answer["GET"] = MaxRetryError(None, "http://apiserver")
    with pytest.raises(ClusterUnavailable):
        reader.list_jobs()


class _HungBody:
    """A response whose headers arrived and whose body never does."""

    @property
    def data(self) -> bytes:
        raise ReadTimeoutError(None, "http://apiserver", "read timed out")


def test_a_body_that_never_arrives_is_the_same_exception(reader, monkeypatch):
    """The body is read after the call returns, so a timeout there escaped
    as a bare 500 rather than the 502 every other silence gets."""
    monkeypatch.setattr(client.ApiClient, "call_api", lambda *a, **k: _HungBody())
    with pytest.raises(ClusterUnavailable):
        reader.get_job("htr-a", "kyrk")
    with pytest.raises(ClusterUnavailable):
        reader.list_configmaps()


def _every_call(reader: Reader) -> None:
    reader.list_jobs()
    reader.list_warmups()
    reader.get_job("htr-a", "kyrk")
    reader.get_configmap("htr-a", "campaign-kyrk")
    reader.list_configmaps()
    reader.list_pods("htr-a", "kyrk")
    reader.apply_configmap(RECORD)


def test_no_call_can_wait_on_the_api_server_for_ever(reader: Reader):
    """Without a timeout a connection that hangs holds its worker thread
    until the process dies, and a detail request makes several calls in a
    row: a few of those fill the pool (2026-09-23 audit)."""
    reader.answer["GET"] = {"items": []}
    _every_call(reader)
    assert {c["method"] for c in reader.calls} == {"GET", "PATCH"}
    assert all(c["timeout"] == kube.REQUEST_TIMEOUT for c in reader.calls)
    connect, read = kube.REQUEST_TIMEOUT
    assert 0 < connect <= read <= 30


def test_an_apply_that_never_answers_is_the_cluster_being_unavailable(
    reader: Reader,
):
    reader.answer["PATCH"] = MaxRetryError(None, "http://apiserver")
    with pytest.raises(ClusterUnavailable):
        reader.apply_configmap(RECORD)


RECORD = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "campaign-kyrk-status", "namespace": "htr-a"},
    "data": {"phase": "Running"},
}


def test_the_record_is_applied_the_way_a_server_side_apply_is(reader: Reader):
    """The content type is the whole difference between a server-side apply
    and a strategic-merge patch, which would merge `data` instead of
    replacing it and never drop a key this service stopped writing."""
    reader.apply_configmap(RECORD)
    (call,) = reader.calls
    assert call["method"] == "PATCH"
    assert call["path"] == "/api/v1/namespaces/htr-a/configmaps/campaign-kyrk-status"
    assert call["content_type"] == "application/apply-patch+yaml"
    assert call["query"]["fieldManager"] == FIELD_MANAGER
    assert call["body"] == RECORD


def test_the_failures_are_applied_as_their_own_manager(reader: Reader):
    reader.apply_configmap(RECORD, force=True, manager=projection.FAILURES_MANAGER)
    assert reader.calls[0]["query"]["fieldManager"] == "htrflow-web-failures"


def test_the_apply_never_forces_another_managers_field(reader: Reader):
    """`htrflow-campaigns apply` writes this same record from the live Job
    once a campaign is over, and its terminal values are the authoritative
    ones. Forcing would take them over on every poll of the status page."""
    reader.apply_configmap(RECORD)
    assert "force" not in reader.calls[0]["query"]


def test_a_forced_apply_says_so_and_sends_the_version_it_read(reader: Reader):
    """Only for a record of another Job (projection.record_write): forced,
    with the resourceVersion the request read in the body -- the API server
    treats it as a precondition, so apply's write in between is a 409."""
    body = {**RECORD, "metadata": {**RECORD["metadata"], "resourceVersion": "7"}}
    reader.apply_configmap(body, force=True)
    (call,) = reader.calls
    assert call["query"]["force"] is True
    assert call["body"]["metadata"]["resourceVersion"] == "7"


def test_a_conflict_is_not_sent_again(reader: Reader):
    """A 409 on a server-side apply is a field another manager owns, or a
    precondition this request's read no longer meets -- the same request
    sent again meets the same answer (2026-09-23 review). It stands as an
    ``ApplyConflict``, and the next poll reads the record afresh."""
    reader.answer["PATCH"] = [_api_error(409), {}]
    with pytest.raises(ApplyConflict):
        reader.apply_configmap(RECORD)
    assert len(reader.calls) == 1


def test_a_conflict_is_not_reported_as_the_cluster_being_unavailable(
    reader, monkeypatch
):
    reader.answer["PATCH"] = _api_error(409)
    with pytest.raises(Exception) as caught:
        reader.apply_configmap(RECORD)
    assert not isinstance(caught.value, ClusterUnavailable)


def test_a_refused_apply_is_still_the_cluster_saying_no(reader: Reader):
    reader.answer["PATCH"] = _api_error(403)
    with pytest.raises(ClusterUnavailable):
        reader.apply_configmap(RECORD)


def test_the_metadata_list_asks_for_the_list_form():
    """A list request must name PartialObjectMetadataList, not the single
    object form: the API server answers 406 to the latter and does not fall
    back to the plain JSON offered after it (a 1.35 server, live)."""
    from htrflow_web.kube import PARTIAL_METADATA

    first = PARTIAL_METADATA.split(",")[0]
    assert "as=PartialObjectMetadataList;" in first
