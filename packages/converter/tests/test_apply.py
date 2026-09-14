"""``htrflow-campaigns apply`` against a fake API server.

The real command is the only thing between a campaigns repo and the cluster,
so what is asserted here is the *decisions* it makes: pipelines before
campaigns (a campaign Job mounts its pipeline's ConfigMap), a prune that
deletes only labelled objects this render did not produce, and the Kueue
pause sync patching a Workload only when its ``spec.active`` disagrees with
what git says.

``FakeCluster`` subclasses the real ``Cluster`` and replaces **only the API
server** — the list/patch/delete methods and the Kueue custom-object client
— with dictionaries, so ``Cluster.prune`` and ``Cluster.sync_pause``
themselves are the code under test here. What goes on the wire (the
server-side apply content type, the label selector, background propagation)
is asserted in ``test_cluster.py`` against the real client.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from kubernetes.client.exceptions import ApiException

from htrflow_converter import cli
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import Cluster
from htrflow_converter.render import CAMPAIGN_SELECTOR

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = FIXTURES / "good"
NS = "htr-test"


class _Body:
    """What ``_preload_content=False`` hands back: raw response bytes."""

    def __init__(self, obj: dict) -> None:
        self.data = json.dumps(obj).encode()


def _labelled(obj: dict, selector: str) -> bool:
    key, value = selector.split("=", 1)
    return obj["metadata"].get("labels", {}).get(key) == value


class _Kueue:
    """``CustomObjectsApi``, over a ``{job-uid: workload}`` map."""

    def __init__(self, outer: "FakeCluster") -> None:
        self.outer = outer

    def list_namespaced_custom_object(self, *args, label_selector: str = "", **kw):
        uid = label_selector.split("=")[-1]
        wl = self.outer.workloads.get(uid)
        return {"items": [wl] if wl else []}

    def patch_namespaced_custom_object(
        self, group, version, ns, plural, name, body, **kw
    ):
        self.outer.calls.append(("patch", name, body["spec"]["active"]))


class FakeCluster(Cluster):
    """The real ``Cluster`` with dictionaries where the API server was.

    ``live`` is what the cluster already holds (each entry ``kind``, ``name``
    and its labels — an object with no converter label must survive a prune);
    ``workloads`` maps a Job uid to its Kueue Workload. An applied Job gets
    the uid ``uid-<name>``, which is how a test wires the two together.
    """

    def __init__(self) -> None:
        self.namespace = ""
        self.live: list[dict] = []
        self.applied: dict[str, dict] = {}
        self.managers: dict[str, str | None] = {}
        self.workloads: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def made(self, namespace: str) -> "FakeCluster":
        self.namespace = namespace
        return self

    def _method(self, kind: str, verb: str, name: str = ""):
        def patch(name, ns, obj, **kw):
            self.calls.append(("apply", kind, name))
            self.applied[name] = obj
            self.managers[name] = kw.get("field_manager")
            return _Body({"metadata": {"name": name, "uid": f"uid-{name}"}})

        def list_(ns, label_selector="", **kw):
            items = [
                o
                for o in self.live
                if o["kind"] == kind and _labelled(o, label_selector)
            ]
            return _Body({"items": items})

        def delete(name, ns, **kw):
            self.calls.append(("delete", kind, name))

        def read(name, ns, **kw):
            for o in self.live:
                if o["kind"] == kind and o["metadata"]["name"] == name:
                    return _Body(o)
            raise ApiException(status=404, reason="Not Found")

        return {"patch": patch, "list": list_, "delete": delete, "read": read}[verb]

    @property
    def custom(self):
        return _Kueue(self)

    def of(self, verb: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == verb]


def _object(kind: str, name: str, labelled: bool = True) -> dict:
    labels = dict([CAMPAIGN_SELECTOR.split("=")]) if labelled else {}
    return {"kind": kind, "metadata": {"name": name, "labels": labels}}


def _workload(name: str, active: bool | None) -> dict:
    spec = {} if active is None else {"active": active}
    return {"metadata": {"name": name}, "spec": spec}


@pytest.fixture
def cluster(monkeypatch):
    c = FakeCluster()
    monkeypatch.setattr(cli, "_cluster", c.made)
    monkeypatch.setattr(cluster_mod.time, "sleep", lambda _: None)
    return c


def _repo(tmp_path, *, paused: str | None = None) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(GOOD, repo)
    if paused:
        (repo / "campaigns" / f"{paused}.yaml").write_text(
            "pipeline: demo-v1\nsuspend: true\nvolumes:\n  - R7777777\n"
        )
    return repo


def test_pipelines_are_applied_before_campaigns(tmp_path, cluster):
    """A campaign's Job mounts its pipeline's ConfigMap and waits on that
    pipeline's warm-up Job, so the order is not cosmetic."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("apply") == [
        ("apply", "ConfigMap", "htr-pipeline-demo-v1"),
        ("apply", "Job", "htr-warmup-demo-v1"),
        ("apply", "ConfigMap", "campaign-kyrk"),
        ("apply", "Job", "kyrk"),
        ("apply", "ConfigMap", "campaign-loc"),
        ("apply", "Job", "loc"),
    ]
    assert cluster.namespace == NS, "the namespace comes from converter.yaml"


def test_apply_without_out_renders_into_a_temp_dir(tmp_path, cluster):
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo)]) == 0
    assert len(cluster.of("apply")) == 6


def test_nothing_is_deleted_without_prune(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_object("Job", "cancelled")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("delete") == []


def test_prune_deletes_only_unrendered_labelled_objects(tmp_path, cluster, capsys):
    """``--prune`` is what makes deleting a campaign file cancel the
    campaign. It must not touch a pipeline object this apply just wrote
    (pruning against campaigns/ alone would), and it must not touch anything
    the converter does not own — the label is the whole boundary."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [
        _object("Job", "kyrk"),  # rendered: kept
        # The record of a campaign still in git: kept, and it has no TTL,
        # so it is what is left once the Job is reaped (B76).
        _object("ConfigMap", "campaign-kyrk"),
        # Written by the read API, never rendered -- kept while its campaign
        # is in git, pruned with it when it is not (B76).
        _object("ConfigMap", "campaign-kyrk-status"),
        _object("ConfigMap", "campaign-cancelled-status"),
        _object("Job", "htr-warmup-demo-v1"),  # a pipeline object: kept
        _object("Job", "cancelled"),  # gone from git: pruned
        _object("ConfigMap", "campaign-cancelled"),  # its volumes.txt: pruned
        _object("Job", "someone-elses", labelled=False),  # not ours: untouched
    ]
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 0
    assert cluster.of("delete") == [
        ("delete", "Job", "cancelled"),
        ("delete", "ConfigMap", "campaign-cancelled-status"),
        ("delete", "ConfigMap", "campaign-cancelled"),
    ]
    assert "pruned: Job/cancelled" in capsys.readouterr().out


def test_the_workload_is_patched_only_when_active_differs(tmp_path, cluster):
    """Git is the truth about what should run: a paused campaign's Workload
    is deactivated, a running one's is re-activated (Kueue deactivates a
    Workload on its own for a requeue limit). Both are no-ops when the
    cluster already agrees, so an apply of an unchanged repo is silent."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {
        "uid-kyrk": _workload("job-kyrk-a", None),  # unset == active: agrees
        "uid-loc": _workload("job-loc-b", False),  # Kueue deactivated it
        "uid-pausy": _workload("job-pausy-c", True),  # paused in git
    }
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("patch") == [
        ("patch", "job-loc-b", True),
        ("patch", "job-pausy-c", False),
    ]


def test_a_paused_campaign_whose_workload_never_appears_waits_then_fails(
    tmp_path, cluster, capsys
):
    """The window in which a brand-new paused campaign has no Workload yet is
    exactly the window in which Kueue admits and starts it. Skipping it would
    run a campaign git says is paused, so the apply waits and then fails."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "3"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "pausy: paused in git" in captured.err
    assert "kyrk: no Workload yet, skipping" in captured.out


def test_a_running_campaign_without_a_workload_is_skipped(tmp_path, cluster):
    """A Workload that does not exist is not admitted either — the next
    apply catches it. Only a *paused* campaign has to wait."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "3"]) == 0
    assert cluster.of("patch") == []


def test_dry_run_renders_but_reaches_no_cluster(tmp_path, monkeypatch, capsys):
    def boom(namespace):
        raise AssertionError("--dry-run built a cluster client")

    monkeypatch.setattr(cli, "_cluster", boom)
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    assert (
        cli.main(["apply", str(repo), "--out", str(out), "--dry-run", "--prune"]) == 0
    )
    assert (out / "campaigns" / "pausy.yaml").exists()
    printed = capsys.readouterr().out
    assert "would apply: ConfigMap/htr-pipeline-demo-v1" in printed
    assert "would apply: Job/pausy" in printed
    assert f"would prune: every {CAMPAIGN_SELECTOR}" in printed


def test_a_cluster_error_prints_the_sentence_and_exits_1(tmp_path, cluster, capsys):
    """``cli._apply`` must turn a ``ClusterError`` into a one-sentence stderr
    line and exit 1 -- not let it explode into a traceback."""
    sentence = (
        "not allowed to apply Job/kyrk in htr-test: Forbidden — the "
        "htrflow-batch chart renders the needed ServiceAccount behind "
        "apply.rbac.enabled"
    )

    def boom(kind, verb, name=""):
        if verb == "patch":
            raise cluster_mod.ClusterError(sentence)
        return FakeCluster._method(cluster, kind, verb, name)

    cluster._method = boom
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    rc = cli.main(["apply", str(repo), "--out", str(out)])
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == sentence
    assert captured.out.count("Traceback") == 0


def test_a_render_error_never_reaches_the_cluster(tmp_path, cluster, capsys):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    (repo / "pipelines" / "demo-v1.yaml").write_text("image: nodigest\nsteps: []\n")
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert cluster.calls == []
    assert "digest" in capsys.readouterr().out


# --- the campaign ConfigMap is the record (B76) -------------------------


def _annotations(cluster, name: str) -> dict:
    return cluster.applied[name]["metadata"]["annotations"]


def test_the_campaign_configmap_records_who_applied_what_and_when(
    tmp_path, cluster, monkeypatch
):
    """The ConfigMap has no TTL and is pruned only when the campaign file
    leaves git, so it outlives the Job -- which makes it the place the
    provenance belongs (B76)."""
    if shutil.which("git") is None:
        pytest.skip("no git on PATH — the CI image has none; _git_head says unknown")
    monkeypatch.setenv("HTRFLOW_APPLIED_BY", "Nagon.Annan")
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@e",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    ann = _annotations(cluster, "campaign-kyrk")
    assert ann["htrflow.riksarkivet.se/campaigns-commit"] == head
    assert ann["htrflow.riksarkivet.se/applied-by"] == "nagon.annan"
    # `submitter` is the multi-tenant spec's (D10): a LABEL stamped at
    # render time by CI from an authenticated login. This apply knows only
    # who ran it, which is a different claim under a different key (B94).
    assert "htrflow.riksarkivet.se/submitter" not in ann
    assert ann["htrflow.riksarkivet.se/applied-at"].endswith("Z")
    # Rendered, not stamped here: it is a pure function of the repo.
    assert ann["htrflow.riksarkivet.se/image-digest"].startswith("ghcr.io/")


def test_a_campaigns_directory_outside_git_records_an_unknown_commit(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    ann = _annotations(cluster, "campaign-kyrk")
    assert ann["htrflow.riksarkivet.se/campaigns-commit"] == "unknown"


def test_the_provenance_is_not_written_into_the_rendered_files(tmp_path, cluster):
    """`rendered/` must stay a pure function of the repo (B78): who applied
    it and when are not, so they are stamped on the way to the API server."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    text = (out / "campaigns" / "kyrk.yaml").read_text()
    assert "applied-at" not in text and "applied-by" not in text


# --- a finished campaign is not re-run once its Job is reaped (B76) ------

VOLUMES = (
    "R0001203\thttps://lbiiif.riksarkivet.se/arkis!R0001203/manifest\n"
    "dodsbok-1698\thttps://iiif.example.org/xyz/manifest\n"
    "loose-scans\timages:https://example.org/scan1.jpg,https://example.org/scan2.jpg\n"
)


def _record(name: str, volumes: str = VOLUMES) -> dict:
    cm = _object("ConfigMap", f"campaign-{name}")
    cm["data"] = {"volumes.txt": volumes}
    return cm


def _status(name: str, phase: str, **data) -> dict:
    cm = _object("ConfigMap", f"campaign-{name}-status")
    cm["data"] = {
        "phase": phase,
        "volumesTotal": "3",
        "volumesDone": "3",
        "volumesFailed": "0",
        "finishedAt": "2026-09-08T10:00:00Z",
        **data,
    }
    return cm


def test_a_finished_campaign_whose_job_was_reaped_is_left_alone(
    tmp_path, cluster, capsys
):
    """The whole point of B76: the Job is gone (TTL), so an apply would
    recreate it and re-run every volume. The record says it is done and the
    volume list has not moved, so nothing is sent for it."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_record("kyrk"), _status("kyrk", "Succeeded")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    applied = [c[2] for c in cluster.of("apply")]
    assert "kyrk" not in applied and "campaign-kyrk" not in applied
    assert "loc" in applied, "the other campaign is applied as usual"
    assert "campaign kyrk finished 2026-09-08, unchanged, left alone" in (
        capsys.readouterr().out
    )


def test_a_campaign_that_failed_is_also_finished(tmp_path, cluster):
    """A campaign that gave up is over too: re-running it is the same GPU
    bill for the same failures."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_record("kyrk"), _status("kyrk", "PartiallyFailed")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" not in [c[2] for c in cluster.of("apply")]


def test_a_running_campaign_is_applied_as_before(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_record("kyrk"), _status("kyrk", "Running")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" in [c[2] for c in cluster.of("apply")]


def test_a_campaign_with_no_record_at_all_is_applied(tmp_path, cluster):
    """A campaign nobody has ever applied has no status ConfigMap."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" in [c[2] for c in cluster.of("apply")]


def test_a_finished_campaign_whose_volumes_moved_is_still_applied(tmp_path, cluster):
    """`rendered/` is what the append-only rule compares against, and this
    repo's rendered/ was written by this very run -- so a live record whose
    volumes.txt disagrees is a campaign that was changed outside it. Left to
    the apply (and to the append-only rule the next render runs)."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [
        _record("kyrk", "other\thttps://x/manifest\n"),
        _status("kyrk", "Succeeded"),
    ]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" in [c[2] for c in cluster.of("apply")]


def test_the_apply_records_a_finished_job_nobody_looked_at(tmp_path, cluster, capsys):
    """The read API writes the record only while a person has the status
    page open. This apply finds the Job still there and finished, records
    that, and then -- in the same run -- leaves the campaign alone."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["metadata"]["labels"]["htrflow.riksarkivet.se/campaign"] = "kyrk"
    live_job["metadata"]["labels"]["htrflow.riksarkivet.se/pipeline"] = "demo-v1"
    live_job["spec"] = {"completions": 3}
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
        "completionTime": "2026-09-08T10:00:00Z",
    }
    cluster.live = [_record("kyrk"), live_job]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    written = cluster.applied["campaign-kyrk-status"]["data"]
    assert written["phase"] == "Succeeded"
    assert written["volumesDone"] == "3"
    # And the skip works off what this very apply just wrote.
    assert "kyrk" not in [c[2] for c in cluster.of("apply") if c[1] == "Job"]
    assert "campaign kyrk finished 2026-09-08, unchanged, left alone" in (
        capsys.readouterr().out
    )


def test_a_running_job_is_not_recorded_by_the_apply(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    live_job = _object("Job", "kyrk")
    live_job["spec"] = {"completions": 3}
    live_job["status"] = {"conditions": [], "active": 1}
    cluster.live = [_record("kyrk"), live_job]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "campaign-kyrk-status" not in cluster.applied
    assert "kyrk" in [c[2] for c in cluster.of("apply")]


def test_a_cluster_that_refuses_the_record_does_not_stop_the_apply(
    tmp_path, cluster, capsys
):
    """Recording how a campaign ended is an improvement on the apply, never
    a precondition for it. An identity whose Role predates B76 -- or a
    human's restricted kubeconfig -- has no `get` on Jobs, and refusing to
    apply anything at all over that would take the campaigns repo offline
    for a permission it never needed before."""

    def forbidden(kind, verb, name=""):
        if verb == "read":
            raise cluster_mod.ClusterError(
                f"not allowed to get {kind}/{name} in htr-test: Forbidden"
            )
        return FakeCluster._method(cluster, kind, verb, name)

    cluster._method = forbidden
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    applied = [c[2] for c in cluster.of("apply")]
    assert applied == [
        "htr-pipeline-demo-v1",
        "htr-warmup-demo-v1",
        "campaign-kyrk",
        "kyrk",
        "campaign-loc",
        "loc",
    ]
    err = capsys.readouterr().err
    assert err.count("could not record how campaign kyrk ended") == 1
    assert "Forbidden" in err


def test_a_refused_record_write_does_not_stop_the_apply(tmp_path, cluster, capsys):
    """The same, one step later: the Job reads fine and the record write is
    what is refused."""
    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["spec"] = {"completions": 3}
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
    }
    cluster.live = [live_job]

    def forbidden(kind, verb, name=""):
        if verb == "patch" and name.endswith("-status"):
            raise cluster_mod.ClusterError(f"not allowed to patch {name}: Forbidden")
        return FakeCluster._method(cluster, kind, verb, name)

    cluster._method = forbidden
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" in [c[2] for c in cluster.of("apply")]
    assert "could not record how campaign kyrk ended" in capsys.readouterr().err


def test_the_applys_record_cannot_erase_the_failed_volumes_the_api_wrote(
    tmp_path, cluster
):
    """Server-side apply owns fields per manager. The apply writes under
    `htrflow-campaigns` and never sets `failedVolumes` at all, so the
    sentences the read API wrote under its own manager stay: one record,
    two writers, no field either of them can take from the other by
    omission (B76)."""
    from htrflow_web.kube import FIELD_MANAGER as WEB_MANAGER

    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["spec"] = {"completions": 3}
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
    }
    cluster.live = [live_job]
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    written = cluster.applied["campaign-kyrk-status"]
    assert "failedVolumes" not in written["data"]
    assert cluster.managers["campaign-kyrk-status"] == cluster_mod.FIELD_MANAGER
    assert cluster_mod.FIELD_MANAGER != WEB_MANAGER
