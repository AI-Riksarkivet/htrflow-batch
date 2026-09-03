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
from pathlib import Path

import pytest

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
        self.workloads: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def made(self, namespace: str) -> "FakeCluster":
        self.namespace = namespace
        return self

    def _method(self, kind: str, verb: str):
        def patch(name, ns, obj, **kw):
            self.calls.append(("apply", kind, name))
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

        return {"patch": patch, "list": list_, "delete": delete}[verb]

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
        _object("Job", "htr-warmup-demo-v1"),  # a pipeline object: kept
        _object("Job", "cancelled"),  # gone from git: pruned
        _object("ConfigMap", "campaign-cancelled"),  # its volumes.txt: pruned
        _object("Job", "someone-elses", labelled=False),  # not ours: untouched
    ]
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 0
    assert cluster.of("delete") == [
        ("delete", "Job", "cancelled"),
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
    out_text = capsys.readouterr().out
    assert "pausy: paused in git" in out_text
    assert "kyrk: no Workload yet, skipping" in out_text


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


def test_a_render_error_never_reaches_the_cluster(tmp_path, cluster, capsys):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    (repo / "pipelines" / "demo-v1.yaml").write_text("image: nodigest\nsteps: []\n")
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert cluster.calls == []
    assert "digest" in capsys.readouterr().out
