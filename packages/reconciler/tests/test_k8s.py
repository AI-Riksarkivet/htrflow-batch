"""The kube adapter against stubbed API clients that return real
``kubernetes.client`` models and honour label selectors (audit T3): the
selector is what keeps hand-run Jobs out of the submission window, so a
fake that ignores it proves nothing."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from kubernetes import client, config

from htrflow_reconciler import k8s
from htrflow_reconciler.k8s import Cluster, lease_is_free

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
NS = "htr-batch"


# -- lease_is_free (pure) ------------------------------------------------------


def _spec(holder, renewed_ago: int, duration: int = 600):
    return SimpleNamespace(
        holder_identity=holder,
        acquire_time=NOW - timedelta(seconds=renewed_ago),
        renew_time=NOW - timedelta(seconds=renewed_ago),
        lease_duration_seconds=duration,
    )


def test_lease_free_when_unheld_or_ours():
    assert lease_is_free(None, "me", NOW)
    assert lease_is_free(_spec(None, 0), "me", NOW)
    assert lease_is_free(_spec("me", 0), "me", NOW)


def test_lease_held_by_a_live_tick_is_busy():
    assert not lease_is_free(_spec("other", 30), "me", NOW)


def test_lease_of_a_dead_tick_expires():
    """A tick killed by the deadline never releases; its Lease must not wedge
    every later tick."""
    assert lease_is_free(_spec("other", 601), "me", NOW)


# -- stub API clients ----------------------------------------------------------


def _api_error(status: int) -> client.ApiException:
    return client.ApiException(status=status, reason=f"stub {status}")


def _selected(labels: dict | None, selector: str) -> bool:
    """Equality-based selector semantics: every ``k=v`` term must match."""
    labels = labels or {}
    for term in filter(None, selector.split(",")):
        key, _, value = term.partition("=")
        if labels.get(key) != value:
            return False
    return True


def _job(
    name: str,
    labels: dict,
    *,
    conditions: list[tuple[str, str, str | None]] = (),
    active: int = 0,
    failed_pods: int = 0,
    deletion: datetime | None = None,
) -> client.V1Job:
    return client.V1Job(
        metadata=client.V1ObjectMeta(
            name=name, labels=labels, deletion_timestamp=deletion
        ),
        status=client.V1JobStatus(
            active=active,
            failed=failed_pods,
            conditions=[
                client.V1JobCondition(type=t, status=s, reason=r)
                for t, s, r in conditions
            ],
        ),
    )


def _pod(
    job: str, name: str, created: datetime, exit_code: int | None = None
) -> client.V1Pod:
    terminated = (
        client.V1ContainerStateTerminated(exit_code=exit_code)
        if exit_code is not None
        else None
    )
    status = client.V1ContainerStatus(
        name="wrapper",
        image="i",
        image_id="i",
        ready=False,
        restart_count=0,
        state=client.V1ContainerState(terminated=terminated),
    )
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name,
            creation_timestamp=created,
            labels={"batch.kubernetes.io/job-name": job},
        ),
        status=client.V1PodStatus(container_statuses=[status]),
    )


class StubBatch:
    def __init__(self, jobs: list[client.V1Job]):
        self._jobs = jobs
        self.selectors: list[str] = []
        self.created: list[dict] = []
        self.deleted: list[tuple[str, str | None]] = []
        self.create_error: int | None = None
        self.delete_error: int | None = None

    def list_namespaced_job(self, ns, label_selector=""):
        assert ns == NS
        self.selectors.append(label_selector)
        items = [j for j in self._jobs if _selected(j.metadata.labels, label_selector)]
        return client.V1JobList(items=items)

    def create_namespaced_job(self, ns, body):
        assert ns == NS
        if self.create_error:
            raise _api_error(self.create_error)
        self.created.append(body)

    def delete_namespaced_job(self, ns, name, propagation_policy=None):
        assert ns == NS
        self.deleted.append((name, propagation_policy))
        if self.delete_error:
            raise _api_error(self.delete_error)


class StubCore:
    def __init__(self, pods: list[client.V1Pod], configmaps: dict[str, str]):
        self._pods = pods
        self._configmaps = configmaps
        self.logs: dict[str, bytes] = {}
        self.log_error: int | None = None
        self.log_requests: list[tuple[str, int]] = []
        self.created: list[dict] = []
        self.create_error: int | None = None
        self.read_error: int | None = None

    def list_namespaced_pod(self, ns, label_selector=""):
        assert ns == NS
        items = [p for p in self._pods if _selected(p.metadata.labels, label_selector)]
        return client.V1PodList(items=items)

    def read_namespaced_config_map(self, name, ns):
        assert ns == NS
        if self.read_error:
            raise _api_error(self.read_error)
        if name not in self._configmaps:
            raise _api_error(404)
        return client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name),
            data={"pipeline.yaml": self._configmaps[name]},
        )

    def create_namespaced_config_map(self, ns, body):
        assert ns == NS
        if self.create_error:
            raise _api_error(self.create_error)
        self.created.append(body)

    def read_namespaced_pod_log(self, name, ns, tail_lines=None, _preload_content=True):
        assert ns == NS and _preload_content is False
        self.log_requests.append((name, tail_lines))
        if self.log_error:
            raise _api_error(self.log_error)
        return SimpleNamespace(data=self.logs[name])


class StubCoord:
    def __init__(self, leases: dict[str, client.V1Lease]):
        self.leases = leases
        self.replace_error: int | None = None
        self.read_error: int | None = None
        self.replaced: list[client.V1Lease] = []

    def read_namespaced_lease(self, name, ns):
        assert ns == NS
        if self.read_error:
            raise _api_error(self.read_error)
        if name not in self.leases:
            raise _api_error(404)
        return self.leases[name]

    def create_namespaced_lease(self, ns, body):
        assert ns == NS
        if body.metadata.name in self.leases:
            raise _api_error(409)
        self.leases[body.metadata.name] = body

    def replace_namespaced_lease(self, name, ns, body):
        assert ns == NS
        if self.replace_error:
            raise _api_error(self.replace_error)
        self.leases[name] = body
        self.replaced.append(body)


@pytest.fixture
def make_cluster(monkeypatch):
    """A ``Cluster`` whose API clients are the stubs above. The constructor
    itself runs: in-cluster config is refused (as on a developer host) and
    the kubeconfig fallback is stubbed so no real file is read."""

    def _make(jobs=(), pods=(), configmaps=None, leases=None, holder="tick-1"):
        batch = StubBatch(list(jobs))
        core = StubCore(list(pods), dict(configmaps or {}))
        coord = StubCoord(dict(leases or {}))

        def refuse():
            raise config.ConfigException("not in a pod")

        monkeypatch.setattr(config, "load_incluster_config", refuse)
        monkeypatch.setattr(config, "load_kube_config", lambda: None)
        monkeypatch.setattr(client, "BatchV1Api", lambda: batch)
        monkeypatch.setattr(client, "CoreV1Api", lambda: core)
        monkeypatch.setattr(client, "CoordinationV1Api", lambda: coord)
        monkeypatch.setattr(k8s.socket, "gethostname", lambda: holder)
        cluster = Cluster(NS)
        assert cluster.holder == holder
        return cluster, batch, core, coord

    return _make


MANAGED = {"app": "htrflow-batch", "batch.htrflow/managed-by": "reconciler"}


# -- jobs() / warmups() -------------------------------------------------------


def test_jobs_selects_managed_by_reconciler_and_app(make_cluster):
    """A hand-run Job shares the ``app`` label and has no TTL; without the
    managed-by term it would hold a window slot forever (audit T3)."""
    cluster, batch, _, _ = make_cluster(
        jobs=[
            _job("htr-managed", MANAGED, active=1),
            _job("htr-by-hand", {"app": "htrflow-batch"}, active=1),
            _job("other-app", {"batch.htrflow/managed-by": "reconciler"}, active=1),
        ]
    )
    assert set(cluster.jobs()) == {"htr-managed"}
    assert batch.selectors == ["app=htrflow-batch,batch.htrflow/managed-by=reconciler"]


def test_warmups_are_keyed_by_pipeline_label_on_app_alone(make_cluster):
    """Helm-rendered warm-ups (values.pipelines) carry no managed-by label and
    are equally proof the cache is warm."""
    cluster, batch, _, _ = make_cluster(
        jobs=[
            _job(
                "htr-warmup-demo-v1",
                {"app": "htrflow-warmup", "batch.htrflow/pipeline": "demo-v1"},
                conditions=[("Complete", "True", None)],
            ),
            _job(
                "htr-warmup-helm",
                {"app": "htrflow-warmup", "batch.htrflow/pipeline": "helm-v2"},
                active=1,
            ),
            _job("htr-volume", MANAGED, active=1),
        ]
    )
    warm = cluster.warmups()
    assert set(warm) == {"demo-v1", "helm-v2"}
    assert warm["demo-v1"].succeeded and not warm["helm-v2"].succeeded
    assert batch.selectors == ["app=htrflow-warmup"]


def test_failed_is_the_terminal_condition_not_the_pod_count(make_cluster):
    """A Job whose first pod failed but which is still retrying reports
    ``status.failed == 1`` — that is not a failed Job."""
    cluster, _, core, _ = make_cluster(
        jobs=[_job("htr-a", MANAGED, active=1, failed_pods=1)],
        pods=[_pod("htr-a", "htr-a-x", NOW, exit_code=1)],
    )
    state = cluster.jobs()["htr-a"]
    assert (state.active, state.failed, state.succeeded) == (True, False, False)
    assert state.exit_code is None  # pods are only consulted for failed Jobs
    assert not core.log_requests


def test_failed_false_condition_is_not_failed(make_cluster):
    cluster, _, _, _ = make_cluster(
        jobs=[_job("htr-a", MANAGED, active=1, conditions=[("Failed", "False", None)])]
    )
    assert cluster.jobs()["htr-a"].failed is False


def test_complete_condition_is_succeeded(make_cluster):
    cluster, _, _, _ = make_cluster(
        jobs=[_job("htr-a", MANAGED, conditions=[("Complete", "True", None)])]
    )
    state = cluster.jobs()["htr-a"]
    assert (state.active, state.failed, state.succeeded) == (False, False, True)


def test_deletion_timestamp_marks_a_deleting_job(make_cluster):
    stamp = datetime(2026, 8, 26, 9, 59, tzinfo=timezone.utc)
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job(
                "htr-a",
                MANAGED,
                conditions=[("Failed", "True", "BackoffLimitExceeded")],
                deletion=stamp,
            )
        ]
    )
    state = cluster.jobs()["htr-a"]
    assert state.deleting and state.in_flight
    assert state.deletion_timestamp == stamp.isoformat()


def test_exit_code_comes_from_the_newest_pod(make_cluster):
    """Exit code and logs must describe the SAME pod: the newest one."""
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", MANAGED, conditions=[("Failed", "True", "PodFailurePolicy")])
        ],
        pods=[
            _pod("htr-a", "htr-a-old", NOW - timedelta(minutes=5), exit_code=1),
            _pod("htr-a", "htr-a-new", NOW, exit_code=13),
            _pod("htr-b", "htr-b-new", NOW + timedelta(minutes=1), exit_code=7),
        ],
    )
    assert cluster.jobs()["htr-a"].exit_code == 13


def test_exit_code_skips_pods_without_a_terminated_container(make_cluster):
    """The newest pod may still be Pending/Running (or have been replaced
    before it terminated); the verdict comes from the newest one that did."""
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", MANAGED, conditions=[("Failed", "True", "DeadlineExceeded")])
        ],
        pods=[
            _pod("htr-a", "htr-a-old", NOW - timedelta(minutes=5), exit_code=1),
            _pod("htr-a", "htr-a-new", NOW, exit_code=None),
        ],
    )
    assert cluster.jobs()["htr-a"].exit_code == 1


def test_exit_code_is_none_without_any_terminated_container(make_cluster):
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", MANAGED, conditions=[("Failed", "True", "DeadlineExceeded")])
        ],
        pods=[_pod("htr-a", "htr-a-new", NOW, exit_code=None)],
    )
    assert cluster.jobs()["htr-a"].exit_code is None


def test_exit_code_is_none_when_the_pod_is_gone(make_cluster):
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", MANAGED, conditions=[("Failed", "True", "PodFailurePolicy")])
        ]
    )
    assert cluster.jobs()["htr-a"].exit_code is None


def test_failed_condition_reason_is_surfaced(make_cluster):
    """The pod may be reaped by the time the reconciler looks (R6); the
    Failed condition's reason is the fallback verdict."""
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", MANAGED, conditions=[("Failed", "True", "PodFailurePolicy")]),
            _job("htr-b", MANAGED, conditions=[("Failed", "True", "DeadlineExceeded")]),
            _job("htr-c", MANAGED, active=1),
        ]
    )
    states = cluster.jobs()
    assert states["htr-a"].reason == "PodFailurePolicy"
    assert states["htr-b"].reason == "DeadlineExceeded"
    assert states["htr-c"].reason is None


def test_campaign_label_is_carried(make_cluster):
    cluster, _, _, _ = make_cluster(
        jobs=[
            _job("htr-a", {**MANAGED, "batch.htrflow/campaign": "trolldom"}, active=1),
            _job("htr-b", MANAGED, active=1),
        ]
    )
    states = cluster.jobs()
    assert states["htr-a"].campaign == "trolldom"
    assert states["htr-b"].campaign is None


def test_job_without_status_fields_reads_as_queued(make_cluster):
    """A freshly created (suspended) Job has ``status: {}``: active None,
    no conditions."""
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name="htr-a", labels=MANAGED),
        status=client.V1JobStatus(),
    )
    cluster, _, _, _ = make_cluster(jobs=[job])
    state = cluster.jobs()["htr-a"]
    assert (state.active, state.failed, state.succeeded) == (False, False, False)
    assert state.in_flight


# -- create / delete / configmaps ---------------------------------------------


def test_create_job_swallows_409_and_raises_the_rest(make_cluster):
    cluster, batch, _, _ = make_cluster()
    cluster.create_job({"metadata": {"name": "htr-a"}})
    assert batch.created == [{"metadata": {"name": "htr-a"}}]
    batch.create_error = 409
    cluster.create_job({"metadata": {"name": "htr-a"}})  # AlreadyExists: a race
    batch.create_error = 403
    with pytest.raises(client.ApiException) as e:
        cluster.create_job({"metadata": {"name": "htr-a"}})
    assert e.value.status == 403


def test_delete_job_is_foreground_and_swallows_404(make_cluster):
    """Foreground: the Job stays listed (Terminating) until its pod is gone,
    so the GPU it may still hold keeps counting against the window."""
    cluster, batch, _, _ = make_cluster()
    cluster.delete_job("htr-a")
    batch.delete_error = 404
    cluster.delete_job("htr-gone")
    assert batch.deleted == [("htr-a", "Foreground"), ("htr-gone", "Foreground")]
    batch.delete_error = 500
    with pytest.raises(client.ApiException):
        cluster.delete_job("htr-a")


def test_get_configmap_steps_returns_text_or_none(make_cluster):
    cluster, _, core, _ = make_cluster(
        configmaps={"htr-pipeline-demo-v1": "steps:\n  - step: Segmentation\n"}
    )
    assert cluster.get_configmap_steps("demo-v1").startswith("steps:")
    assert cluster.get_configmap_steps("absent") is None
    core.read_error = 403
    with pytest.raises(client.ApiException):
        cluster.get_configmap_steps("demo-v1")


def test_get_configmap_steps_without_the_key_is_none(make_cluster):
    cluster, _, core, _ = make_cluster()
    core.read_namespaced_config_map = lambda name, ns: client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=name), data=None
    )
    assert cluster.get_configmap_steps("demo-v1") is None


def test_ensure_configmap_creates_once_and_tolerates_409(make_cluster):
    cluster, _, core, _ = make_cluster()
    cluster.ensure_configmap("demo-v1", "steps: []\n")
    assert core.created == [
        {
            "metadata": {"name": "htr-pipeline-demo-v1"},
            "data": {"pipeline.yaml": "steps: []\n"},
        }
    ]
    core.create_error = 409
    cluster.ensure_configmap("demo-v1", "steps: []\n")
    core.create_error = 500
    with pytest.raises(client.ApiException):
        cluster.ensure_configmap("demo-v1", "steps: []\n")


# -- job_logs -----------------------------------------------------------------


def test_job_logs_reads_the_newest_pod_decoded(make_cluster):
    cluster, _, core, _ = make_cluster(
        pods=[
            _pod("htr-a", "htr-a-old", NOW - timedelta(minutes=5)),
            _pod("htr-a", "htr-a-new", NOW),
        ]
    )
    core.logs["htr-a-new"] = "ERROR boom \xe5\n".encode("utf-8")
    core.logs["htr-a-old"] = b"older attempt\n"
    assert cluster.job_logs("htr-a", tail=7) == "ERROR boom \xe5\n"
    assert core.log_requests == [("htr-a-new", 7)]


def test_job_logs_is_empty_without_pods_or_on_api_error(make_cluster):
    cluster, _, core, _ = make_cluster(pods=[_pod("htr-a", "htr-a-x", NOW)])
    assert cluster.job_logs("htr-nobody") == ""
    core.log_error = 400  # e.g. container still creating
    assert cluster.job_logs("htr-a") == ""


def test_job_logs_replaces_undecodable_bytes(make_cluster):
    cluster, _, core, _ = make_cluster(pods=[_pod("htr-a", "htr-a-x", NOW)])
    core.logs["htr-a-x"] = b"bad \xff byte\n"
    assert cluster.job_logs("htr-a") == "bad � byte\n"


# -- Lease --------------------------------------------------------------------


def _lease(holder: str | None, renewed_ago: int, duration: int = 600) -> client.V1Lease:
    now = datetime.now(timezone.utc)
    return client.V1Lease(
        metadata=client.V1ObjectMeta(name="htr-reconciler"),
        spec=client.V1LeaseSpec(
            holder_identity=holder,
            acquire_time=now - timedelta(seconds=renewed_ago),
            renew_time=now - timedelta(seconds=renewed_ago),
            lease_duration_seconds=duration,
        ),
    )


def test_acquire_creates_a_missing_lease(make_cluster):
    cluster, _, _, coord = make_cluster()
    assert cluster.acquire_lease("htr-reconciler", 600) is True
    lease = coord.leases["htr-reconciler"]
    assert lease.spec.holder_identity == "tick-1"
    assert lease.spec.lease_duration_seconds == 600
    assert lease.spec.renew_time is not None


def test_acquire_loses_the_create_race(make_cluster):
    cluster, _, _, coord = make_cluster()
    real_read = coord.read_namespaced_lease

    def read_then_appear(name, ns):
        coord.leases[name] = _lease("tick-2", 0)  # someone created it meanwhile
        coord.read_namespaced_lease = real_read
        raise _api_error(404)

    coord.read_namespaced_lease = read_then_appear
    assert cluster.acquire_lease("htr-reconciler", 600) is False


def test_acquire_refuses_a_held_lease(make_cluster):
    cluster, _, _, coord = make_cluster(leases={"htr-reconciler": _lease("tick-2", 30)})
    assert cluster.acquire_lease("htr-reconciler", 600) is False
    assert coord.replaced == []


def test_acquire_takes_over_an_expired_lease(make_cluster):
    """A tick killed by the deadline left its Lease behind; past
    renewTime + duration it is free."""
    cluster, _, _, coord = make_cluster(
        leases={"htr-reconciler": _lease("tick-2", 601, duration=600)}
    )
    assert cluster.acquire_lease("htr-reconciler", 600) is True
    assert coord.leases["htr-reconciler"].spec.holder_identity == "tick-1"
    assert len(coord.replaced) == 1


def test_acquire_reuses_our_own_lease(make_cluster):
    cluster, _, _, coord = make_cluster(leases={"htr-reconciler": _lease("tick-1", 30)})
    assert cluster.acquire_lease("htr-reconciler", 600) is True
    assert len(coord.replaced) == 1


def test_acquire_loses_the_replace_race(make_cluster):
    cluster, _, _, coord = make_cluster(
        leases={"htr-reconciler": _lease("tick-2", 601)}
    )
    coord.replace_error = 409
    assert cluster.acquire_lease("htr-reconciler", 600) is False


def test_acquire_raises_on_rbac_errors(make_cluster):
    """Without the leases RBAC the tick must fail loudly, not run unguarded."""
    cluster, _, _, coord = make_cluster()
    coord.read_error = 403
    with pytest.raises(client.ApiException) as e:
        cluster.acquire_lease("htr-reconciler", 600)
    assert e.value.status == 403


def test_release_clears_our_lease_only(make_cluster):
    cluster, _, _, coord = make_cluster(leases={"htr-reconciler": _lease("tick-1", 5)})
    cluster.release_lease("htr-reconciler")
    spec = coord.leases["htr-reconciler"].spec
    assert spec.holder_identity is None and spec.renew_time is None

    coord.leases["htr-reconciler"] = _lease("tick-2", 5)
    cluster.release_lease("htr-reconciler")
    assert coord.leases["htr-reconciler"].spec.holder_identity == "tick-2"


def test_release_is_best_effort(make_cluster):
    cluster, _, _, coord = make_cluster()
    cluster.release_lease("htr-reconciler")  # 404: nothing to release
    coord.leases["htr-reconciler"] = _lease("tick-1", 5)
    coord.replace_error = 500
    cluster.release_lease("htr-reconciler")  # swallowed: the duration expires it


# -- constructor ---------------------------------------------------------------


def test_cluster_prefers_in_cluster_config(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "load_incluster_config", lambda: calls.append("in"))
    monkeypatch.setattr(config, "load_kube_config", lambda: calls.append("kube"))
    monkeypatch.setattr(client, "BatchV1Api", lambda: None)
    monkeypatch.setattr(client, "CoreV1Api", lambda: None)
    monkeypatch.setattr(client, "CoordinationV1Api", lambda: None)
    cluster = Cluster("ns1")
    assert calls == ["in"] and cluster.ns == "ns1"
