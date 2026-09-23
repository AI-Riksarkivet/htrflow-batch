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

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from kubernetes.client.exceptions import ApiException

from htrflow_converter import cli, render
from htrflow_converter import cluster as cluster_mod
from htrflow_converter.cluster import Cluster
from htrflow_converter.parse import load
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
            if kw.get("dry_run"):
                self.calls.append(("dry-run", kind, name))
                return _Body(obj)
            self.calls.append(("apply", kind, name))
            self.applied[name] = obj
            self.managers[name] = kw.get("field_manager")
            # What the next apply finds: the cluster keeps what it was sent,
            # which is what lets a test run a second apply against the state
            # the first one left (3084).
            stored = copy.deepcopy(obj)
            stored["metadata"]["uid"] = f"uid-{name}"
            # An apply never touches `status` -- the API server keeps what
            # the controllers wrote, and hands it back with the object.
            for o in self.live:
                if o["kind"] == kind and o["metadata"]["name"] == name:
                    if "status" in o:
                        stored["status"] = o["status"]
            self.live = [
                o
                for o in self.live
                if not (o["kind"] == kind and o["metadata"]["name"] == name)
            ] + [stored]
            return _Body(stored)

        def list_(ns, label_selector="", **kw):
            items = [
                o
                for o in self.live
                if o["kind"] == kind and _labelled(o, label_selector)
            ]
            return _Body({"items": items})

        def delete(name, ns, **kw):
            self.calls.append(("delete", kind, name))
            self.live = [
                o
                for o in self.live
                if not (o["kind"] == kind and o["metadata"]["name"] == name)
            ]

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
    line -- not let it explode into a traceback. A server that refuses
    *everything* (a Role without apply, a webhook rejecting the lot) is the
    total failure it always was: exit 1, not the refused-object code."""
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
    lines = captured.err.strip().splitlines()
    # Each campaign's ConfigMap is held back with its refused Job (3084).
    assert {line for line in lines[:-1] if "left as it was" not in line} == {sentence}
    assert lines[-1].endswith("the other 0 were applied (exit 1)")
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
    # Rendered, not stamped here: it is a pure function of the repo -- the
    # very image reference the campaign's pipeline file pins, compared whole
    # rather than by a registry prefix that could sit anywhere in it.
    pipelines = load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")[1]
    assert ann["htrflow.riksarkivet.se/image-digest"] == pipelines["demo-v1"].image


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


def _rendered_volumes(name: str = "kyrk") -> str:
    """What the converter writes into this campaign's ConfigMap, taken from
    the renderer rather than spelled out here: `apply` leaves a campaign
    alone only when the stored `volumes.txt` matches the rendered one byte
    for byte, so a test that hardcoded the line format would go quietly
    green the day that format changed."""
    campaigns, pipelines, cfg = load(
        GOOD / "campaigns", GOOD / "pipelines", GOOD / "converter.yaml"
    )
    c = next(campaign for campaign in campaigns if campaign.name == name)
    objects = render.campaign_objects(c, pipelines[c.pipeline], cfg)
    return objects[0]["data"]["volumes.txt"]


VOLUMES = _rendered_volumes()


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


def test_a_finished_campaign_whose_volumes_moved_is_refused(tmp_path, cluster, capsys):
    """This repo has no committed `rendered/`, so the render has nothing to
    hold the campaign against -- and the live record disagrees with it. That
    record used to be overwritten with the new list while the finished Job
    ran none of it, and the NEXT apply read the overwritten record, found it
    matching, and left the campaign alone: volumes reported done that no pod
    ever ran (3084). The live ConfigMap is the record; nothing is sent."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    moved = "other\thttps://x/manifest\n"
    cluster.live = [_record("kyrk", moved), _status("kyrk", "Succeeded")]
    for _ in range(2):
        assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
        assert "campaign kyrk is in the cluster with different volumes" in (
            capsys.readouterr().err
        )
    assert cluster.of("apply") == [] and cluster.of("dry-run") == []
    assert _live(cluster, "campaign-kyrk")["data"]["volumes.txt"] == moved


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


def _unreadable(cluster, kind: str, suffix: str = "") -> None:
    """Make every read of ``kind`` (named ``*suffix``) fail as forbidden."""

    def forbidden(k, verb, name=""):
        if verb == "read" and k == kind and name.endswith(suffix):
            raise cluster_mod.ClusterError(
                f"not allowed to get {k}/{name} in htr-test: Forbidden"
            )
        return FakeCluster._method(cluster, k, verb, name)

    cluster._method = forbidden


def test_a_campaign_whose_job_cannot_be_read_is_left_as_it_was(
    tmp_path, cluster, capsys
):
    """Whether a campaign has finished is what keeps a reaped one from being
    run again, so a check that cannot be made is not a check that passed:
    applying it anyway re-ran a finished campaign's every volume over a
    transient 5xx or a missing `get` (3093). Each such campaign is skipped,
    named, and the apply exits non-zero; the pipelines still go out."""
    _unreadable(cluster, "Job")
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    applied = [c[2] for c in cluster.of("apply")]
    assert applied == ["htr-pipeline-demo-v1", "htr-warmup-demo-v1"]
    assert cluster.of("dry-run") == []
    err = capsys.readouterr().err
    assert "could not tell whether campaign kyrk has finished" in err
    assert "Forbidden" in err
    assert "Job/kyrk" in err.splitlines()[-1] and "Job/loc" in err.splitlines()[-1]


def test_a_status_record_that_cannot_be_read_fails_closed(tmp_path, cluster, capsys):
    """The same when the Job is gone and it is the stored record that cannot
    be read -- exactly the reaped campaign the record exists to protect."""
    _unreadable(cluster, "ConfigMap", "-status")
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    applied = [c[2] for c in cluster.of("apply")]
    assert "kyrk" not in applied and "campaign-kyrk" not in applied
    assert "could not tell whether campaign kyrk has finished" in (
        capsys.readouterr().err
    )


def test_an_unread_paused_campaign_is_an_unenforced_pause(tmp_path, cluster, capsys):
    """Skipped is not paused: a campaign git says is paused that this apply
    could not look at never reaches the pause sync. Exit 1, like any other
    pause that does not hold."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    path = repo / "campaigns" / "kyrk.yaml"
    path.write_text(path.read_text() + "suspend: true\n")
    _unreadable(cluster, "Job")
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert ("apply", "Job", "kyrk") not in cluster.calls
    assert "kyrk: paused in git, but" in capsys.readouterr().err


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


def test_a_refused_record_write_still_lets_the_live_job_decide(
    tmp_path, cluster, capsys
):
    """Losing the write must not lose the decision. The finished Job still
    says this campaign is over -- no stored record needed -- and re-running
    it would cost the whole GPU bill over a permission the decision did not
    need (B76)."""
    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["spec"] = {"completions": 3}
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
        "completionTime": "2026-09-08T10:00:00Z",
    }
    cluster.live = [live_job, _record("kyrk")]

    def forbidden(kind, verb, name=""):
        if verb == "patch" and name.endswith("-status"):
            raise cluster_mod.ClusterError(f"not allowed to patch {name}: Forbidden")
        return FakeCluster._method(cluster, kind, verb, name)

    cluster._method = forbidden
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" not in [c[2] for c in cluster.of("apply") if c[1] == "Job"]
    captured = capsys.readouterr()
    assert "could not record how campaign kyrk ended" in captured.err
    assert "campaign kyrk finished 2026-09-08, unchanged, left alone" in captured.out


def test_a_stored_comma_line_is_the_same_volume_list_as_a_rendered_space_one(
    tmp_path, cluster, capsys
):
    """`images:` URLs used to be comma-joined. A campaign applied before that
    changed has the comma line in its ConfigMap, and this render writes the
    space line -- the same volumes, said twice. Compared byte for byte, the
    campaign reads as changed, and a finished one whose Job the TTL reaped is
    applied again: every volume re-run over a separator. The append-only
    check already reads a line as what it MEANS; so does this."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    comma = VOLUMES.replace(
        "images:https://example.org/scan1.jpg https://example.org/scan2.jpg",
        "images:https://example.org/scan1.jpg,https://example.org/scan2.jpg",
    )
    assert comma != VOLUMES, "the fixture must carry an images: volume"
    cluster.live = [_record("kyrk", comma), _status("kyrk", "Succeeded")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" not in [c[2] for c in cluster.of("apply")]
    assert "campaign kyrk finished" in capsys.readouterr().out


def test_a_volume_list_that_really_moved_is_not_swallowed(tmp_path, cluster):
    """The semantic compare must not swallow a real change: a different URL
    is a different campaign, whatever the separator -- refused, since the
    live record says otherwise (3084)."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    moved = VOLUMES.replace("scan2.jpg", "scan9.jpg")
    cluster.live = [_record("kyrk", moved), _status("kyrk", "Succeeded")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert cluster.of("apply") == []


def _refuses(cluster, target: str, error: Exception, times: int = 99) -> None:
    """Make the fake API server refuse one object's apply -- the first
    ``times`` of them, so a test can let a re-created object through."""
    real = FakeCluster._method
    left = [times]

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb != "patch":
            return inner

        def patch(name, ns, obj, **kw):
            if name == target and left[0] > 0:
                left[0] -= 1
                raise error
            return inner(name, ns, obj, **kw)

        return patch

    cluster._method = method


def _immutable_template(name: str) -> Exception:
    from htrflow_converter.cluster import ImmutableField

    return ImmutableField("Job", name, (ImmutableField.POD_TEMPLATE,))


def test_one_refused_object_does_not_stop_the_apply(tmp_path, cluster, capsys):
    """The live failure this exists for: one object the API server would not
    take aborted the whole loop, and the campaign behind it in the order was
    never applied at all. A refusal is now one object's problem."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    _refuses(
        cluster,
        "kyrk",
        cluster_mod.ClusterError("apply Job/kyrk: 409 Conflict"),
    )
    rc = cli.main(["apply", str(repo), "--out", str(out)])
    assert rc == cli.REFUSED, "a refused object is not a total failure"
    # campaign-kyrk is not among them: its Job was refused on the dry run,
    # and a ConfigMap applied without its Job is a volumes.txt the Job's
    # unstarted indexes would read (3084).
    assert [c[2] for c in cluster.of("apply")] == [
        "htr-pipeline-demo-v1",
        "htr-warmup-demo-v1",
        "campaign-loc",
        "loc",
    ]
    err = capsys.readouterr().err
    assert "apply Job/kyrk: 409 Conflict" in err
    assert "Job/kyrk" in err.splitlines()[-1], "the summary names what was refused"


def test_a_refused_campaign_job_still_lets_the_rest_prune(tmp_path, cluster):
    """A refusal stops nothing downstream either: the prune and the pause
    sync are about every OTHER campaign in the repo."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_object("Job", "cancelled")]
    _refuses(cluster, "kyrk", cluster_mod.ClusterError("apply Job/kyrk: 409"))
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == cli.REFUSED
    assert cluster.of("delete") == [("delete", "Job", "cancelled")]


def test_a_warmup_whose_pod_template_changed_is_replaced(tmp_path, cluster, capsys):
    """A converter release, or a converter.yaml setting such as the Hub
    token, renders every warm-up Job's pod template differently -- and a
    Job's template cannot be edited. A warm-up is idempotent (its marker is
    on the cache PVC) and holds no campaign state, so the answer is to
    delete it and create it again rather than to report it forever."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_object("Job", "htr-warmup-demo-v1")]
    _refuses(
        cluster, "htr-warmup-demo-v1", _immutable_template("htr-warmup-demo-v1"), 1
    )
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("delete") == [("delete", "Job", "htr-warmup-demo-v1")]
    # The fake refuses before it records the call, so the one apply of the
    # warm-up in `calls` is the re-create -- and it comes after the delete.
    warmup = [c for c in cluster.calls if c[2] == "htr-warmup-demo-v1"]
    assert warmup == [
        ("delete", "Job", "htr-warmup-demo-v1"),
        ("apply", "Job", "htr-warmup-demo-v1"),
    ]
    printed = capsys.readouterr().out
    assert "replaced: Job/htr-warmup-demo-v1" in printed
    assert "the marker on the cache PVC" in printed


def test_a_running_warmup_is_reported_and_left_alone(tmp_path, cluster, capsys):
    """Deleting a warm-up that is downloading right now throws the download
    away and, worse, takes the pod with it while campaigns wait on its
    marker. That one is reported and re-run on the next apply."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    running = _object("Job", "htr-warmup-demo-v1")
    running["status"] = {"active": 1}
    cluster.live = [running]
    _refuses(cluster, "htr-warmup-demo-v1", _immutable_template("htr-warmup-demo-v1"))
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("delete") == []
    assert "running right now" in capsys.readouterr().err


def test_a_campaign_job_is_never_deleted_to_change_its_template(tmp_path, cluster):
    """A campaign Job's completed indexes and its results ARE the campaign:
    deleting it to take a new pod template would start every volume over.
    A changed pipeline under a live campaign is `render`'s to refuse."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_object("Job", "kyrk")]
    _refuses(cluster, "kyrk", _immutable_template("kyrk"))
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("delete") == []


def test_apply_without_out_holds_pipelines_against_the_committed_render(
    tmp_path, cluster, capsys
):
    """`apply` with no `--out` renders into a temp directory, which records
    nothing. The repo's committed `rendered/` is the record either way --
    otherwise the one command that reaches a cluster is the one command the
    pipeline guard does not run for."""
    repo = _repo(tmp_path)
    assert cli.main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["image"] = "ghcr.io/riksarkivet/htrflow-batch@sha256:" + "b" * 64
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    assert cli.main(["apply", str(repo)]) == 1
    assert "pipeline demo-v1 changed (image)" in capsys.readouterr().out
    assert cluster.of("apply") == [], "nothing reached the cluster"


def test_an_unenforced_pause_outranks_a_refused_object(tmp_path, cluster, capsys):
    """Two things went wrong at once. A campaign git says is paused that is
    running anyway is the graver state -- it is burning GPU right now --
    so its exit 1 wins over the refused object's 3."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    _refuses(cluster, "kyrk", cluster_mod.ClusterError("apply Job/kyrk: 409"))
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pausy: paused in git" in err
    assert "Job/kyrk" in err, "the refusal is still reported"


def test_apply_without_out_holds_campaigns_against_the_committed_render(
    tmp_path, cluster, capsys
):
    """The campaign half of the same rule. A temp render directory holds no
    earlier render, so the append-only check found nothing to compare a
    swapped volume against and applied it -- the one command that reaches a
    cluster being the one command the guard did not run for."""
    repo = _repo(tmp_path)
    assert cli.main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    path = repo / "campaigns" / "kyrk.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["volumes"][0] = "R9999999"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    assert cli.main(["apply", str(repo)]) == 1
    assert "campaign kyrk is append-only" in capsys.readouterr().out
    assert cluster.of("apply") == [], "nothing reached the cluster"


def test_prune_refuses_a_render_that_produced_no_campaigns(tmp_path, cluster, capsys):
    """A typo in the directory, or a checkout that never happened, renders
    zero campaigns -- and `--prune` then deletes every campaign the converter
    manages in the namespace, which is the whole archive's work. Refused
    unless cancelling them all is said out loud."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    for path in (repo / "campaigns").glob("*.yaml"):
        path.unlink()
    cluster.live = [_object("Job", "kyrk"), _object("ConfigMap", "campaign-kyrk")]
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1
    assert cluster.of("delete") == []
    err = capsys.readouterr().err
    assert str(repo / "campaigns") in err
    assert "--allow-empty" in err


def test_allow_empty_prunes_the_last_campaign_away(tmp_path, cluster):
    """The way out the sentence names: deleting the last campaign file IS how
    a campaign is cancelled, so the empty render has to be applicable."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    for path in (repo / "campaigns").glob("*.yaml"):
        path.unlink()
    cluster.live = [_object("Job", "kyrk")]
    rc = cli.main(["apply", str(repo), "--out", str(out), "--prune", "--allow-empty"])
    assert rc == 0
    assert cluster.of("delete") == [("delete", "Job", "kyrk")]


def test_a_repo_without_a_converter_yaml_is_refused_before_the_cluster(
    tmp_path, cluster, capsys
):
    """Without converter.yaml every setting silently defaults -- including
    the namespace -- so an apply meant for one cluster's campaigns lands on
    another's objects, and a --prune deletes them."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    (repo / "converter.yaml").unlink()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert cluster.calls == []
    assert "converter.yaml" in capsys.readouterr().out


def test_a_refused_paused_campaign_job_is_an_unenforced_pause(
    tmp_path, cluster, capsys
):
    """A refused Job never reaches the Kueue sync, so a campaign git says is
    paused went on running with the apply reporting only "some objects were
    refused" (exit 3). The pause is what is not enforced here, and that is
    exit 1 -- the same answer as a Workload that never appeared."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    _refuses(cluster, "pausy", cluster_mod.ClusterError("apply Job/pausy: 409"))
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pausy: paused in git, but the API server refused its Job" in err
    assert "Job/pausy" in err, "the refusal itself is still reported"


def test_an_incomplete_rendered_campaign_is_a_sentence_not_a_keyerror(
    tmp_path, cluster, monkeypatch, capsys
):
    """A campaign is rendered as a pair -- the ConfigMap with its volumes.txt
    and the Job that mounts it. Reading a directory where the pair has come
    apart (a half-finished write, a hand edit, a bad merge of `rendered/`)
    raised a bare KeyError on the Job's name, from inside the loop that is
    about to apply it."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    (out / "campaigns").mkdir(parents=True)
    (out / "pipelines").mkdir()
    (out / "campaigns" / "kyrk.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": "kyrk", "namespace": NS},
                "spec": {},
            }
        )
    )
    monkeypatch.setattr(cli, "_render", lambda *a, **kw: 0)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "campaign kyrk" in err and "re-render" in err
    assert cluster.of("apply") == [], "nothing reached the cluster"


def test_apply_to_a_fresh_out_dir_still_holds_campaigns_against_rendered(
    tmp_path, cluster, capsys
):
    """`--out` says where this render is written, never what it is held
    against: the committed `rendered/` is the record. An `--out` pointing
    somewhere empty is the same blind spot as a temp directory, and it is the
    one an operator reaches for when a render looks wrong."""
    repo = _repo(tmp_path)
    assert cli.main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    path = repo / "campaigns" / "kyrk.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["volumes"][0] = "R9999999"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    capsys.readouterr()

    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "fresh")]) == 1
    assert "campaign kyrk is append-only" in capsys.readouterr().out
    assert cluster.of("apply") == [], "nothing reached the cluster"


def test_dry_run_previews_an_empty_prune_and_the_real_run_still_refuses(
    tmp_path, cluster, capsys
):
    """`--dry-run` is how an operator checks a prune before running it, so
    the one case worth checking hardest -- a render with no campaigns at all
    -- must be previewable: it prints what would go, says the real run will
    refuse, and then the real run does."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    for path in (repo / "campaigns").glob("*.yaml"):
        path.unlink()
    rc = cli.main(["apply", str(repo), "--out", str(out), "--prune", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert f"would prune: every {CAMPAIGN_SELECTOR}" in captured.out
    assert "--allow-empty" in captured.err
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1


# --- a prune problem is one object's problem, never the pause's (3090) ---


def _delete_fails(cluster, target: str, status: int) -> None:
    """Make the fake API server answer ``status`` to deleting ``target``."""
    real = FakeCluster._method

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb != "delete":
            return inner

        def delete(name, ns, **kw):
            if name == target:
                raise ApiException(status=status, reason="refused")
            return inner(name, ns, **kw)

        return delete

    cluster._method = method


def test_a_prune_error_is_followed_by_the_pause_sync(tmp_path, cluster, capsys):
    """One Job the prune could not delete used to end the apply before the
    Kueue sync ran, so a campaign git says is paused kept running -- zero
    Workload patches, and nothing on stderr about the pause. The pause is
    enforced whatever the prune met, and the prune's refusal is reported."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {"uid-pausy": _workload("job-pausy-c", True)}
    cluster.live = [_object("Job", "cancelled"), _object("Job", "cancelled-too")]
    _delete_fails(cluster, "cancelled", 403)
    rc = cli.main(["apply", str(repo), "--out", str(out), "--prune"])
    assert cluster.of("patch") == [("patch", "job-pausy-c", False)]
    assert ("delete", "Job", "cancelled-too") in cluster.calls, "the prune goes on"
    assert rc == cli.REFUSED, "a Job still to delete is a change still to make"
    err = capsys.readouterr().err
    assert "not allowed to delete Job/cancelled" in err
    assert "Job/cancelled" in err.splitlines()[-1], "the summary names it"


def test_a_job_the_ttl_reaped_first_is_pruned_not_an_error(tmp_path, cluster, capsys):
    """The TTL controller can delete a finished Job between the prune's list
    and its delete. The 404 that answers is the prune's goal reached."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {"uid-pausy": _workload("job-pausy-c", True)}
    cluster.live = [_object("Job", "cancelled")]
    _delete_fails(cluster, "cancelled", 404)
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 0
    assert cluster.of("patch") == [("patch", "job-pausy-c", False)]
    assert capsys.readouterr().err == ""


# --- the live cluster, not rendered/ alone, is what apply answers to (3084)


def _live(cluster, name: str) -> dict:
    return next(o for o in cluster.live if o["metadata"]["name"] == name)


def _edit(path: Path, **changes) -> None:
    doc = yaml.safe_load(path.read_text())
    doc.update(changes)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_second_apply_cannot_swap_the_volumes_the_first_applied(
    tmp_path, cluster, capsys
):
    """No committed `rendered/` -- a fresh checkout, a repo whose render was
    never pushed, a `rendered/` reset by a bad merge -- used to mean no
    append-only rule at all: the second apply replaced the live volumes.txt
    under a Job whose completions were fixed by the first."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    before = _live(cluster, "campaign-kyrk")["data"]["volumes.txt"]
    kyrk = repo / "campaigns" / "kyrk.yaml"
    _edit(kyrk, volumes=["R9999999", *yaml.safe_load(kyrk.read_text())["volumes"][1:]])
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 1
    err = capsys.readouterr().err
    assert "campaign kyrk is in the cluster with different volumes" in err
    assert "nothing was applied" in err
    assert cluster.of("apply") == [], "refused before anything was sent"
    assert _live(cluster, "campaign-kyrk")["data"]["volumes.txt"] == before


def test_a_second_apply_cannot_move_a_live_campaign_to_another_pipeline(
    tmp_path, cluster, capsys
):
    """The Job would be refused (its pod template is fixed), but only after
    its ConfigMap had been re-labelled and the new pipeline's objects
    written. Held against the live record, it is refused before any of it."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    v1 = repo / "pipelines" / "demo-v1.yaml"
    (repo / "pipelines" / "demo-v2.yaml").write_text(v1.read_text())
    _edit(repo / "campaigns" / "kyrk.yaml", pipeline="demo-v2")
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 1
    assert "campaign kyrk is in the cluster with different pipeline" in (
        capsys.readouterr().err
    )
    assert cluster.of("apply") == []


def test_a_moved_campaign_still_holds_the_pipeline_it_was_rendered_with(
    tmp_path, cluster, capsys
):
    """Move every campaign off demo-v1 and edit demo-v1 in the same change:
    the pipeline guard counted demo-v1's users from `campaigns/`, found none
    and let the edit through -- rewriting the pipeline ConfigMap under Jobs
    that are still running it. Its users are what `rendered/` recorded."""
    repo = _repo(tmp_path)
    assert cli.main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    assert cli.main(["apply", str(repo)]) == 0
    v1 = repo / "pipelines" / "demo-v1.yaml"
    (repo / "pipelines" / "demo-v2.yaml").write_text(v1.read_text())
    for name in ("kyrk", "loc"):
        _edit(repo / "campaigns" / f"{name}.yaml", pipeline="demo-v2")
    _edit(v1, image="ghcr.io/riksarkivet/htrflow-batch@sha256:" + "b" * 64)
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["validate", str(repo)]) == 1
    assert cli.main(["apply", str(repo)]) == 1
    out = capsys.readouterr().out
    assert (
        "pipeline demo-v1 changed (image) but campaigns kyrk, loc still run it" in out
    )
    assert cluster.calls == [], "the render refuses it; no cluster is asked"


def test_a_refused_campaign_job_leaves_its_configmap_as_it_was(tmp_path, cluster):
    """A Job the API server refuses must not have its ConfigMap applied
    first: the indexes that have not started read volumes.txt, not the Job.
    Every campaign Job is tried with dryRun=All before its pair is sent."""
    repo = _repo(tmp_path)
    _refuses(cluster, "kyrk", cluster_mod.ClusterError("apply Job/kyrk: 422"))
    assert cli.main(["apply", str(repo)]) == cli.REFUSED
    assert ("dry-run", "Job", "kyrk") not in cluster.calls, "refused, not recorded"
    assert ("dry-run", "Job", "loc") in cluster.calls
    applied = [c[2] for c in cluster.of("apply")]
    assert "campaign-kyrk" not in applied and "kyrk" not in applied
    assert applied.index("loc") > applied.index("campaign-loc")


def test_a_live_record_it_may_not_read_stops_the_apply(tmp_path, cluster, capsys):
    """The live ConfigMap is the one thing this apply holds a campaign
    against. When it cannot be read, the check cannot be made, and applying
    anyway is exactly the silent overwrite the check is there to stop."""

    def forbidden(kind, verb, name=""):
        if verb == "read" and kind == "ConfigMap":
            raise cluster_mod.ClusterError(
                f"not allowed to get {kind}/{name} in htr-test: Forbidden"
            )
        return FakeCluster._method(cluster, kind, verb, name)

    cluster._method = forbidden
    assert cli.main(["apply", str(_repo(tmp_path))]) == 1
    assert "not allowed to get ConfigMap/campaign-kyrk" in capsys.readouterr().err
    assert cluster.of("apply") == [] and cluster.of("dry-run") == []


def test_a_server_lost_mid_apply_stops_it_rather_than_refusing_the_rest(
    tmp_path, cluster, capsys
):
    """A read timeout on one object's apply is not that object refused: the
    API server may well have taken it, and every object after it would pay
    its own connect timeouts against a server that is not there. It is
    retried, then the apply stops and says to re-run -- exit 1, never the
    "refused and unchanged" of exit 3 (3091)."""
    from urllib3.exceptions import ReadTimeoutError

    repo, out = _repo(tmp_path), tmp_path / "rendered"
    _refuses(
        cluster,
        "campaign-kyrk",
        ReadTimeoutError(pool=None, url="/", message="Read timed out."),
    )
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1
    applied = [c[2] for c in cluster.of("apply")]
    assert applied == ["htr-pipeline-demo-v1", "htr-warmup-demo-v1"]
    assert cluster.of("delete") == [], "no prune after a lost server"
    err = capsys.readouterr().err
    assert "cannot reach the Kubernetes API server" in err
    assert "stopped at ConfigMap/campaign-kyrk" in err
    assert "refused by the API server" not in err


def _running_job(name: str) -> dict:
    job = _object("Job", name)
    job["metadata"]["uid"] = f"uid-{name}"
    job["spec"] = {"completions": 3}
    job["status"] = {"conditions": [], "active": 1}
    return job


def test_a_running_job_outranks_a_stale_finished_record(tmp_path, cluster, capsys):
    """A `-status` record that says Succeeded while a live Job is still
    running is left over from something else: a name reused without
    --prune, an Argo CD prune (which never tracks `-status`), a Job
    re-created by hand. It used to win -- "finished, unchanged, left alone",
    exit 0 -- and a campaign git had just paused went on running, since a
    campaign left alone never reaches the pause sync (3083)."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    path = repo / "campaigns" / "kyrk.yaml"
    path.write_text(path.read_text() + "suspend: true\n")
    cluster.live = [_record("kyrk"), _status("kyrk", "Succeeded"), _running_job("kyrk")]
    cluster.workloads["uid-kyrk"] = _workload("wl-kyrk", True)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") in cluster.calls
    assert cluster.of("patch") == [("patch", "wl-kyrk", False)], "the pause holds"
    assert "left alone" not in capsys.readouterr().out


def _warmup(condition: str) -> dict:
    job = _object("Job", "htr-warmup-demo-v1")
    job["status"] = {"conditions": [{"type": condition, "status": "True"}]}
    return job


def test_a_failed_warmup_is_replaced(tmp_path, cluster, capsys):
    """A warm-up that spent its backoffLimit -- a Secret not there yet, a
    Hub outage -- stays Failed, every campaign on the pipeline fails each
    index on the missing marker, and an unchanged re-apply is a no-op on it
    for ever: the only way out was a `kubectl delete` (3092). It holds no
    state, so the apply runs it again."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_warmup("Failed")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    warmup = [c for c in cluster.calls if c[2] == "htr-warmup-demo-v1"]
    assert warmup == [
        ("apply", "Job", "htr-warmup-demo-v1"),
        ("delete", "Job", "htr-warmup-demo-v1"),
        ("apply", "Job", "htr-warmup-demo-v1"),
    ]
    assert "status" not in _live(cluster, "htr-warmup-demo-v1"), "a fresh Job"
    assert "replaced: Job/htr-warmup-demo-v1 — it had failed" in (
        capsys.readouterr().out
    )


def test_a_finished_warmup_is_left_alone(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_warmup("Complete")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("delete") == []
