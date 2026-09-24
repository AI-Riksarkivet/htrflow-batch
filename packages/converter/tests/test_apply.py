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
import functools
import itertools
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
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
        self.headers: dict[str, str] = {}


def _labelled(obj: dict, selector: str) -> bool:
    """A label selector of ``key=value`` and bare ``key`` terms, ANDed."""
    labels = obj["metadata"].get("labels") or {}
    for term in filter(None, selector.split(",")):
        key, _, value = term.partition("=")
        if key not in labels or (value and labels[key] != value):
            return False
    return True


#: What identifies an object rather than being one of its fields: never
#: owned by a manager, never released.
_IDENTITY = {
    ("apiVersion",),
    ("kind",),
    ("metadata", "name"),
    ("metadata", "namespace"),
}
#: What the API server keeps for itself: an apply that sends these does not
#: set them, and they appear in no manager's field set.
_SERVER_KEPT = {("status",), ("metadata", "uid"), ("metadata", "managedFields")}
#: Fields the API server puts back when the last owner lets go. Only the one
#: this code relies on: a Job's ``spec.suspend`` defaults to false, which is
#: a Job the Job controller starts at once.
_DEFAULTS = {("Job", ("spec", "suspend")): False}
#: What a Job cannot be created without: a partial apply creates nothing.
_TEMPLATE = ("spec", "template")


def _fields(obj: dict, prefix: tuple = ()) -> dict[tuple, object]:
    """Every field path an apply of ``obj`` sets -> its value. A map is
    walked into; a list is one field -- the real API server merges some lists
    by key (a pod's containers by name), and nothing this code applies
    shares a list with another manager, so atomic is exact enough here."""
    out: dict[tuple, object] = {}
    for key, value in obj.items():
        path = (*prefix, key)
        if path in _IDENTITY or path in _SERVER_KEPT:
            continue
        if isinstance(value, dict) and value:
            out.update(_fields(value, path))
        else:
            out[path] = value
    return out


_MISSING = object()


def _at(obj: dict, path: tuple) -> object:
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return _MISSING
        obj = obj[key]
    return obj


def _put(obj: dict, path: tuple, value: object) -> None:
    for key in path[:-1]:
        obj = obj.setdefault(key, {})
    obj[path[-1]] = value


def _drop(obj: dict, path: tuple) -> None:
    parents = [obj]
    for key in path[:-1]:
        obj = obj.get(key)
        if not isinstance(obj, dict):
            return
        parents.append(obj)
    obj.pop(path[-1], None)
    for parent, key in zip(reversed(parents[:-1]), reversed(path[:-1])):
        if parent[key] == {}:
            del parent[key]


def _fields_v1(paths: set[tuple]) -> dict:
    """``paths`` as ``managedFields[].fieldsV1`` spells them: ``f:``-keyed."""
    tree: dict = {}
    for path in paths:
        node = tree
        for key in path:
            node = node.setdefault(f"f:{key}", {})
    return tree


def _immutable(kind: str, name: str, field: str) -> ApiException:
    """The API server's answer to a change of a field fixed at create: 422
    Invalid, the rejected value quoted back, the field named in
    ``details.causes`` -- which is where ``Cluster`` reads it from."""
    e = ApiException(status=422, reason="Unprocessable Entity")
    why = "Invalid value: core.PodTemplateSpec{…}: field is immutable"
    e.body = json.dumps({
        "kind": "Status", "reason": "Invalid", "code": 422,
        "message": f'{kind}.batch "{name}" is invalid: {field}: {why}',
        "details": {"name": name, "group": "batch", "kind": kind, "causes": [
            {"reason": "FieldValueInvalid", "message": why, "field": field},
        ]},
    })  # fmt: skip
    return e


class _Kueue:
    """``CustomObjectsApi``, over a ``{job-uid: workload}`` map."""

    def __init__(self, outer: "FakeCluster") -> None:
        self.outer = outer

    def list_namespaced_custom_object(self, *args, label_selector: str = "", **kw):
        uid = label_selector.split("=")[-1]
        wl = self.outer.workloads.get(uid)
        return {"items": [wl] if wl else []}

    def _read(self, plural: str, name: str) -> dict:
        status = self.outer.kueue_errors.get(plural)
        if status is not None:
            raise ApiException(status=status, reason="Forbidden")
        self.outer.calls.append(("get", plural, name))
        found = self.outer.kueue_objects.get((plural, name))
        if found is None:
            raise ApiException(status=404, reason="Not Found")
        return found

    def get_namespaced_custom_object(self, group, version, ns, plural, name, **kw):
        return self._read(plural, name)

    def get_cluster_custom_object(self, group, version, plural, name, **kw):
        return self._read(plural, name)

    def patch_namespaced_custom_object(
        self, group, version, ns, plural, name, body, **kw
    ):
        status = self.outer.workload_errors.get(name)
        if status is not None:
            raise ApiException(status=status, reason="refused")
        self.outer.calls.append(("patch", name, body["spec"]["active"]))
        for wl in self.outer.workloads.values():
            if wl["metadata"]["name"] == name:
                wl.setdefault("spec", {})["active"] = body["spec"]["active"]


class _Leases:
    """``CoordinationV1Api``, over one ``{name: lease}`` map, holding every
    write to the ``resourceVersion`` it carries, as the API server does."""

    def __init__(self, outer: "FakeCluster") -> None:
        self.outer = outer

    def _answer(self, verb: str, obj: dict) -> _Body:
        status = self.outer.lease_errors.get(verb)
        if status is not None:
            raise ApiException(status=status, reason="refused")
        body = _Body(obj)
        if self.outer.server_time is not None:  # the API server's own clock
            body.headers = {"Date": format_datetime(self.outer.server_time, True)}
        return body

    def read_namespaced_lease(self, name, ns, **kw):
        if name not in self.outer.leases:
            raise ApiException(status=404, reason="Not Found")
        return self._answer("read", self.outer.leases[name])

    def _store(self, name: str, body: dict) -> _Body:
        stored = copy.deepcopy(body)
        stored["metadata"]["resourceVersion"] = str(next(self.outer.versions))
        answer = self._answer("write", stored)
        self.outer.leases[name] = stored
        self.outer.lease_log.append(stored["spec"].get("holderIdentity"))
        return answer

    def create_namespaced_lease(self, ns, body, **kw):
        name = body["metadata"]["name"]
        if name in self.outer.leases:
            raise ApiException(status=409, reason="AlreadyExists")
        return self._store(name, body)

    def replace_namespaced_lease(self, name, ns, body, **kw):
        live = self.outer.leases.get(name)
        if live is None:
            raise ApiException(status=404, reason="Not Found")
        if (
            body["metadata"].get("resourceVersion")
            != live["metadata"]["resourceVersion"]
        ):
            raise ApiException(status=409, reason="Conflict")
        return self._store(name, body)


class FakeCluster(Cluster):
    """The real ``Cluster`` with dictionaries where the API server was.

    ``live`` is what the cluster already holds (each entry ``kind``, ``name``
    and its labels — an object with no converter label must survive a prune);
    ``workloads`` maps a Job uid to its Kueue Workload. Every create gets a
    uid never used before, as the API server gives it: ``uid-<name>`` the
    first time, which is how a test wires the two together, and
    ``uid-<name>-2`` and on for the same name created again.

    Server-side apply is modelled as far as this code leans on it (C16):
    each ``(manager, operation)`` owns a set of fields, written back as
    ``metadata.managedFields``; an apply that would change a field another
    manager owns is a 409 unless forced, and a forced one takes the field
    over; a field two managers set to the same value is theirs jointly; a
    field a manager stops sending is released, and removed -- or put back to
    its default -- once nobody owns it. ``update`` is a controller's write
    (Kueue flipping ``spec.suspend``): it takes whatever it changes. A
    live Job's ``spec.template`` is fixed: an apply that would change it is
    the API server's 422, whoever sends it.
    """

    def __init__(self) -> None:
        self.namespace = ""
        self.live: list[dict] = []
        self.applied: dict[str, dict] = {}
        self.managers: dict[str, str | None] = {}
        self.owners: dict[tuple[str, str], dict[tuple[str, str], set]] = {}
        self.workloads: dict[str, dict] = {}
        self.workload_errors: dict[str, int] = {}
        #: Kueue's queue objects by (plural, name), and a status per plural
        #: to refuse every read of it with.
        self.kueue_objects: dict[tuple[str, str], dict] = {}
        self.kueue_errors: dict[str, int] = {}
        self.leases: dict[str, dict] = {}
        self.lease_log: list[str | None] = []
        self.lease_errors: dict[str, int] = {}
        self.server_time: datetime | None = None
        self.versions = itertools.count(1)
        self.calls: list[tuple] = []
        self.uids: set[str] = set()

    def _new_uid(self, name: str) -> str:
        """A uid no object has had: the API server never hands one out
        twice, and a Job created again under its old name is a new Job."""
        self.uids |= {o["metadata"]["uid"] for o in self.live if "uid" in o["metadata"]}
        uid = f"uid-{name}"
        for n in itertools.count(2):
            if uid not in self.uids:
                break
            uid = f"uid-{name}-{n}"
        self.uids.add(uid)
        return uid

    def made(self, namespace: str) -> "FakeCluster":
        self.namespace = namespace
        return self

    def find(self, kind: str, name: str) -> dict | None:
        found = next(
            (
                o
                for o in self.live
                if o["kind"] == kind and o["metadata"]["name"] == name
            ),
            None,
        )
        if found is not None and "uid" not in found["metadata"]:
            found["metadata"]["uid"] = self._new_uid(name)  # seeded without one
        return found

    def _store(self, kind: str, name: str, obj: dict, owners: dict) -> None:
        obj["metadata"]["managedFields"] = [
            {"manager": m, "operation": op, "fieldsV1": _fields_v1(paths)}
            for (m, op), paths in owners.items()
            if paths
        ]
        self.owners[(kind, name)] = owners
        self.live = [
            o
            for o in self.live
            if not (o["kind"] == kind and o["metadata"]["name"] == name)
        ] + [obj]

    def server_side_apply(
        self, obj: dict, manager: str, force: bool = False, dry_run: bool = False
    ) -> dict:
        kind, name = obj["kind"], obj["metadata"]["name"]
        current = self.find(kind, name)
        if current is None and kind == "Job" and _at(obj, _TEMPLATE) is _MISSING:
            e = ApiException(status=422, reason="Unprocessable Entity")
            e.body = json.dumps({"message": "spec.template: Required value"})
            raise e
        # A dry-run create answers with a uid the write would not keep.
        uid = f"uid-{name}-dry-run" if dry_run else None
        stored = copy.deepcopy(current) if current else {
            "apiVersion": obj.get("apiVersion"), "kind": kind,
            "metadata": {"name": name, "uid": uid or self._new_uid(name)},
        }  # fmt: skip
        for key in ("namespace",):
            if key in obj["metadata"]:
                stored["metadata"][key] = obj["metadata"][key]
        owners = copy.deepcopy(self.owners.get((kind, name), {}))
        mine = (manager, "Apply")
        sent = _fields(obj)
        conflicts = [
            (path, other)
            for path, value in sent.items()
            for other, paths in owners.items()
            if other != mine and path in paths and _at(stored, path) != value
        ]
        if conflicts and not force:
            e = ApiException(status=409, reason="Conflict")
            e.body = json.dumps({"message": "Apply failed with conflicts: " + ", ".join(
                f'conflict with "{m}": .{".".join(p)}' for p, (m, _) in conflicts
            )})  # fmt: skip
            raise e
        for path, other in conflicts:
            owners[other].discard(path)
        for path in owners.get(mine, set()) - set(sent):
            if any(path in paths for m, paths in owners.items() if m != mine):
                continue
            if (kind, path) in _DEFAULTS:
                _put(stored, path, _DEFAULTS[(kind, path)])
            else:
                _drop(stored, path)
        for path, value in sent.items():
            _put(stored, path, copy.deepcopy(value))
        if current is None:  # what the API server defaults on create
            for (k, path), default in _DEFAULTS.items():
                if k == kind and _at(stored, path) is _MISSING:
                    _put(stored, path, default)
        if (
            current is not None
            and kind == "Job"
            and _at(current, _TEMPLATE) != _at(stored, _TEMPLATE)
        ):
            raise _immutable(kind, name, ".".join(_TEMPLATE))
        owners[mine] = set(sent)
        if not dry_run:
            self._store(kind, name, stored, owners)
        return stored

    def update(self, kind: str, name: str, manager: str, changes: dict) -> None:
        """A controller's write (an Update, not an Apply): it owns what it
        changes, and nobody else does any more."""
        stored = copy.deepcopy(self.find(kind, name))
        owners = copy.deepcopy(self.owners.get((kind, name), {}))
        for path, value in _fields(changes).items():
            if _at(stored, path) == value:
                continue
            _put(stored, path, value)
            for paths in owners.values():
                paths.discard(path)
            owners.setdefault((manager, "Update"), set()).add(path)
        self._store(kind, name, stored, owners)

    def _api(self, kind: str, verb: str, name: str = ""):
        def patch(name, ns, obj, **kw):
            if kw.get("dry_run"):
                # Held to the same rules as the real write: a dry run answers
                # what the write would.
                self.calls.append(("dry-run", kind, name))
                return _Body(
                    self.server_side_apply(
                        obj, kw["field_manager"], bool(kw.get("force")), True
                    )
                )
            self.calls.append(("apply", kind, name))
            self.applied[name] = obj
            self.managers[name] = kw.get("field_manager")
            # What the next apply finds: the cluster keeps what it was sent,
            # which is what lets a test run a second apply against the state
            # the first one left (3084). An apply never touches `status` --
            # the API server keeps what the controllers wrote, and hands it
            # back with the object.
            stored = self.server_side_apply(
                obj, kw["field_manager"], force=bool(kw.get("force"))
            )
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
            self.owners.pop((kind, name), None)
            self.live = [
                o
                for o in self.live
                if not (o["kind"] == kind and o["metadata"]["name"] == name)
            ]

        def read(name, ns, **kw):
            found = self.find(kind, name)
            if found is None:
                raise ApiException(status=404, reason="Not Found")
            return _Body(found)

        return {"patch": patch, "list": list_, "delete": delete, "read": read}[verb]

    @property
    def custom(self):
        return _Kueue(self)

    @property
    def coordination(self):
        return _Leases(self)

    def of(self, verb: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == verb]


@functools.cache
def _rendered() -> dict[tuple[str, str], dict]:
    """What the fixture repo renders -- with the paused campaign ``pausy``
    the tests add -- by ``(kind, name)``, from the renderer itself: a live
    Job is seeded as the Job the apply would send, pod template and all,
    so the fake's immutable template holds it the way the API server does."""
    with tempfile.TemporaryDirectory() as t:
        repo = _repo(Path(t), paused="pausy")
        campaigns, pipelines, cfg = load(
            repo / "campaigns", repo / "pipelines", repo / "converter.yaml"
        )
    objects = [o for p in pipelines.values() for o in render.pipeline_objects(p, cfg)]
    for c in campaigns:
        objects += render.campaign_objects(c, pipelines[c.pipeline], cfg)
    return {(o["kind"], o["metadata"]["name"]): o for o in objects}


def _object(kind: str, name: str, labelled: bool = True) -> dict:
    """A live object: a rendered Job as it was rendered, anything else a
    stub carrying the converter's label (or not)."""
    if kind == "Job" and (kind, name) in _rendered():
        return copy.deepcopy(_rendered()[(kind, name)])
    labels = dict([CAMPAIGN_SELECTOR.split("=")]) if labelled else {}
    return {"kind": kind, "metadata": {"name": name, "labels": labels}}


def _stale(job: dict) -> dict:
    """``job`` as an earlier converter release rendered it: its container
    another image, so the rendered pod template is one the live Job cannot
    be changed to."""
    container = job["spec"]["template"]["spec"]["containers"][0]
    container["image"] = container["image"].rsplit("@", 1)[0] + "@sha256:" + "0" * 64
    return job


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
    # A new campaign's ConfigMap is sent twice: once before its Job, and
    # once after, with the uid the new Job got (S-11).
    assert cluster.of("apply") == [
        ("apply", "ConfigMap", "htr-pipeline-demo-v1"),
        ("apply", "Job", "htr-warmup-demo-v1"),
        ("apply", "ConfigMap", "campaign-kyrk"),
        ("apply", "Job", "kyrk"),
        ("apply", "ConfigMap", "campaign-kyrk"),
        ("apply", "ConfigMap", "campaign-loc"),
        ("apply", "Job", "loc"),
        ("apply", "ConfigMap", "campaign-loc"),
    ]
    assert cluster.namespace == NS, "the namespace comes from converter.yaml"


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root`` -> its bytes (``None`` for a directory)."""
    return {
        p.relative_to(root).as_posix(): None if p.is_dir() else p.read_bytes()
        for p in sorted(root.rglob("*"))
    }


def test_apply_without_out_renders_into_a_temp_dir(tmp_path, cluster):
    """`rendered/` is the committed record the render is held against, so an
    apply with no `--out` must leave it -- and the rest of the checkout --
    exactly as it found it, even with a new campaign the record lacks."""
    repo = _repo(tmp_path)
    assert cli.main(["render", str(repo), "--out", str(repo / "rendered")]) == 0
    _rerun(repo, "fresh", "R7777777")
    before = _tree(repo)
    assert cli.main(["apply", str(repo)]) == 0
    assert ("apply", "Job", "fresh") in cluster.calls
    assert _tree(repo) == before


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
        return FakeCluster._api(cluster, kind, verb, name)

    cluster._api = boom
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
    tmp_path, cluster, monkeypatch, commit_all
):
    """The ConfigMap has no TTL and is pruned only when the campaign file
    leaves git, so it outlives the Job -- which makes it the place the
    provenance belongs (B76)."""
    monkeypatch.setenv("HTRFLOW_APPLIED_BY", "Nagon.Annan")
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    head = commit_all(repo)  # read back by git where there is one, else dulwich
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


#: What the converter writes into kyrk's ConfigMap, taken from the renderer:
#: `apply` leaves a campaign alone only when the stored `volumes.txt` matches
#: the rendered one, so a hardcoded line format would go quietly green the
#: day that format changed.
VOLUMES = _rendered()[("ConfigMap", "campaign-kyrk")]["data"]["volumes.txt"]


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
    live_job["status"] = {"conditions": [], "active": 1}
    cluster.live = [_record("kyrk"), live_job]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "campaign-kyrk-status" not in cluster.applied
    assert "kyrk" in [c[2] for c in cluster.of("apply")]


def _read_fails(cluster, fails, status: int = 403) -> None:
    """Make the fake API server answer ``status`` to each read ``fails(kind,
    name)`` picks. Raised by the fake's ``read`` itself -- below
    ``Cluster.get`` -- so what the apply sees is the real mapping of that
    answer, and a ``get`` that took a refused read for "no such object" is
    caught here: a finished campaign re-run, a live check skipped."""
    real = cluster._api  # composes with an injection already in place

    def method(kind, verb, name=""):
        inner = real(kind, verb, name)
        if verb != "read":
            return inner

        def read(name, ns, **kw):
            if fails(kind, name):
                raise ApiException(status=status, reason="Forbidden")
            return inner(name, ns, **kw)

        return read

    cluster._api = method


def _unreadable(cluster, kind: str, suffix: str = "") -> None:
    """Make every read of ``kind`` (named ``*suffix``) fail as forbidden."""
    _read_fails(cluster, lambda k, name: k == kind and name.endswith(suffix))


def test_a_campaign_whose_job_cannot_be_read_is_left_as_it_was(
    tmp_path, cluster, capsys
):
    """Whether a campaign has finished is what keeps a reaped one from being
    run again, so a check that cannot be made is not a check that passed:
    applying it anyway re-ran a finished campaign's every volume over a
    transient 5xx or a missing `get` (3093). Each such campaign is skipped,
    named, and the apply exits non-zero; the pipelines still go out. (Its
    warm-up Job stays readable: one that is not is held back as well, since
    whose it is cannot be told either.)"""
    _read_fails(
        cluster,
        lambda kind, name: kind == "Job" and not name.startswith(render.WARMUP_PREFIX),
    )
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
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
    }
    cluster.live = [live_job]

    def forbidden(kind, verb, name=""):
        if verb == "patch" and name.endswith("-status"):
            raise cluster_mod.ClusterError(f"not allowed to patch {name}: Forbidden")
        return FakeCluster._api(cluster, kind, verb, name)

    cluster._api = forbidden
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "kyrk" in [c[2] for c in cluster.of("apply")]
    assert "could not record how campaign kyrk ended" in capsys.readouterr().err


def test_a_refused_record_write_still_lets_the_live_job_decide(
    tmp_path, cluster, capsys
):
    """Losing the write must not lose the decision. The finished Job still
    says this campaign is over -- no stored record needed -- and re-running
    it would cost the whole GPU bill over a permission the decision did not
    need (B76)."""
    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": 3,
        "completionTime": "2026-09-08T10:00:00Z",
    }
    cluster.live = [live_job, _record("kyrk")]

    def forbidden(kind, verb, name=""):
        if verb == "patch" and name.endswith("-status"):
            raise cluster_mod.ClusterError(f"not allowed to patch {name}: Forbidden")
        return FakeCluster._api(cluster, kind, verb, name)

    cluster._api = forbidden
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
    real = FakeCluster._api
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

    cluster._api = method


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
        "campaign-loc",
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
    cluster.live = [_stale(_object("Job", "htr-warmup-demo-v1"))]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("delete") == [("delete", "Job", "htr-warmup-demo-v1")]
    # The refused apply is recorded before the fake refuses it; the second
    # is the re-create, after the delete.
    warmup = [c for c in cluster.calls if c[2] == "htr-warmup-demo-v1"]
    assert warmup == [
        ("apply", "Job", "htr-warmup-demo-v1"),
        ("delete", "Job", "htr-warmup-demo-v1"),
        ("apply", "Job", "htr-warmup-demo-v1"),
    ]
    template = _live(cluster, "htr-warmup-demo-v1")["spec"]["template"]
    assert template == _object("Job", "htr-warmup-demo-v1")["spec"]["template"]
    printed = capsys.readouterr().out
    assert "replaced: Job/htr-warmup-demo-v1" in printed
    # Not "a file check" for every replacement: a changed recipe warms a
    # cache directory of its own, which is a real download.
    assert "a changed recipe downloads into a cache directory of its own" in printed


def test_a_running_warmup_is_reported_and_left_alone(tmp_path, cluster, capsys):
    """Deleting a warm-up that is downloading right now throws the download
    away and, worse, takes the pod with it while campaigns wait on its
    marker. That one is reported and re-run on the next apply."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    running = _stale(_object("Job", "htr-warmup-demo-v1"))
    running["status"] = {"active": 1}
    cluster.live = [running]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("delete") == []
    assert "running right now" in capsys.readouterr().err


def test_a_campaign_job_is_never_deleted_to_change_its_template(tmp_path, cluster):
    """A campaign Job's completed indexes and its results ARE the campaign:
    deleting it to take a new pod template would start every volume over.
    A changed pipeline under a live campaign is `render`'s to refuse."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_stale(_object("Job", "kyrk"))]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("delete") == []
    template = _live(cluster, "kyrk")["spec"]["template"]
    assert template == _stale(_object("Job", "kyrk"))["spec"]["template"]


def _foreign(kind: str, name: str) -> dict:
    """A same-named object nobody rendered: made by hand, or left from
    before the converter, with data of its own and no converter label."""
    obj = _object(kind, name, labelled=False)
    obj["metadata"]["labels"] = {"made-by": "hand"}
    if kind == "ConfigMap":
        obj["data"] = {"pipeline.yaml": "steps: []\n", "volumes.txt": "R1\n"}
    else:
        obj["spec"] = {"template": {"spec": {"containers": [{"name": "x"}]}}}
    return obj


_NOT_OURS = f"exists in {NS} and was not made by htrflow-campaigns"


def test_an_unlabelled_pipeline_configmap_is_refused_not_taken_over(
    tmp_path, cluster, capsys
):
    """A forced server-side apply takes every field it sends from whoever
    owned it, label included: an admin's apply adopted a hand-made ConfigMap
    of the same name and replaced its data. The label is the boundary the
    prune already keeps, and the apply keeps it too. The warm-up and the
    campaigns on that pipeline mount the ConfigMap by name, so they would
    run its recipe under this pipeline's id: they are held back with it,
    and another pipeline's objects still go out."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    v1 = repo / "pipelines" / "demo-v1.yaml"
    (repo / "pipelines" / "demo-v2.yaml").write_text(v1.read_text())
    (repo / "campaigns" / "other.yaml").write_text(
        "pipeline: demo-v2\nvolumes:\n  - R7777777\n"
    )
    foreign = _foreign("ConfigMap", "htr-pipeline-demo-v1")
    cluster.live = [copy.deepcopy(foreign)]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    live = cluster.find("ConfigMap", "htr-pipeline-demo-v1")
    assert live["data"] == foreign["data"]
    assert live["metadata"]["labels"] == foreign["metadata"]["labels"]
    touched = {c[2] for c in cluster.calls if c[0] in ("apply", "dry-run")}
    assert not touched & {
        "htr-pipeline-demo-v1", "htr-warmup-demo-v1",
        "campaign-kyrk", "kyrk", "campaign-loc", "loc",
    }  # fmt: skip
    assert [c[2] for c in cluster.of("apply")] == [
        "htr-pipeline-demo-v2",
        "htr-warmup-demo-v2",
        "campaign-other",
        "other",
        "campaign-other",
    ]
    err = capsys.readouterr().err
    assert f"ConfigMap htr-pipeline-demo-v1 {_NOT_OURS}" in err
    assert "Job/htr-warmup-demo-v1: left as it was" in err
    assert "ConfigMap/htr-pipeline-demo-v1" in err.splitlines()[-1]


def test_a_labelled_pipeline_configmap_is_applied_as_before(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_object("ConfigMap", "htr-pipeline-demo-v1")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "ConfigMap", "htr-pipeline-demo-v1") in cluster.calls


def test_an_unlabelled_warmup_job_is_refused_never_replaced(tmp_path, cluster, capsys):
    """A warm-up is the one Job the apply deletes to change; one it did not
    make is neither changed nor deleted."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_foreign("Job", "htr-warmup-demo-v1")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("delete") == []
    assert ("apply", "Job", "htr-warmup-demo-v1") not in cluster.calls
    assert f"Job htr-warmup-demo-v1 {_NOT_OURS}" in capsys.readouterr().err


def test_an_unlabelled_campaign_configmap_holds_back_its_job(tmp_path, cluster, capsys):
    """A campaign is a pair: its Job applied beside a ConfigMap it may not
    write would read someone else's volumes.txt."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    foreign = _foreign("ConfigMap", "campaign-kyrk")
    cluster.live = [copy.deepcopy(foreign)]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.find("ConfigMap", "campaign-kyrk")["data"] == foreign["data"]
    touched = {c[2] for c in cluster.calls if c[0] in ("apply", "dry-run")}
    assert not touched & {"campaign-kyrk", "kyrk"}
    assert {"campaign-loc", "loc"} <= touched
    err = capsys.readouterr().err
    assert f"ConfigMap campaign-kyrk {_NOT_OURS}" in err
    assert "ConfigMap/campaign-kyrk, Job/kyrk" in err.splitlines()[-1]


def test_an_unlabelled_job_under_a_campaigns_name_is_left_alone(
    tmp_path, cluster, capsys
):
    """Nor is a Job it did not make read as the campaign's: no status record
    is written from it, and the pair is not sent, dry run included."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_foreign("Job", "kyrk")]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    touched = {c[2] for c in cluster.calls if c[0] in ("apply", "dry-run")}
    assert not touched & {"campaign-kyrk", "campaign-kyrk-status", "kyrk"}
    assert "loc" in touched
    assert f"Job kyrk {_NOT_OURS}" in capsys.readouterr().err


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


def test_a_campaign_whose_job_is_refused_can_still_be_paused(tmp_path, cluster, capsys):
    """After a converter release or a converter.yaml change every live
    campaign Job's pod template differs, and the API server refuses each
    one. Pausing needs none of that: only the live Job's uid and its
    Workload. It used to need the apply to go through, so ``suspend: true``
    got exit 1, "NOT enforced" and zero Workload patches, with kubectl as
    the only way to stop the campaign (C-2)."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.live = [_stale(_running_job("pausy"))]
    cluster.workloads = {"uid-pausy": _workload("wl-pausy", True)}
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "1"])
    assert cluster.of("patch") == [("patch", "wl-pausy", False)], "paused"
    assert rc == cli.REFUSED, "the pause holds; the refused Job is a change to make"
    err = capsys.readouterr().err
    assert "Job pausy: the pod template changed" in err
    assert "NOT enforced" not in err
    assert "Job/pausy" in err.splitlines()[-1]


def test_a_campaign_whose_job_is_refused_can_still_be_unpaused(tmp_path, cluster):
    """The same way out, the other way: a campaign paused before the release
    that refuses its Job could otherwise never be resumed -- and so never
    finish, which is what the refusal asks for."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.live = [_stale(_running_job("kyrk"))]
    cluster.workloads = {"uid-kyrk": _workload("wl-kyrk", False)}
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.of("patch") == [("patch", "wl-kyrk", True)]


def test_a_refused_paused_campaign_with_no_job_has_nothing_to_stop(
    tmp_path, cluster, capsys
):
    """A brand-new paused campaign whose Job was refused has no Job at all:
    nothing is running, so the pause holds. The refusal is reported, and it
    is the refused-object exit, not the unenforced-pause one."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    _refuses(cluster, "pausy", cluster_mod.ClusterError("apply Job/pausy: 409"))
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "1"])
    assert rc == cli.REFUSED
    err = capsys.readouterr().err
    assert "apply Job/pausy: 409" in err and "NOT enforced" not in err


def test_a_refused_paused_campaign_whose_job_cannot_be_read_is_unenforced(
    tmp_path, cluster, capsys
):
    """Without the live Job there is no uid, so no Workload to deactivate:
    that one is the pause not enforced, exit 1."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.live = [_running_job("pausy")]
    _refuses(cluster, "pausy", cluster_mod.ClusterError("apply Job/pausy: 409"))
    reads = [0]

    def second_read_of_pausy(kind, name):
        # The first read (whether it has finished) goes through; the second,
        # for the pause, is refused.
        if (kind, name) == ("Job", "pausy"):
            reads[0] += 1
        return (kind, name) == ("Job", "pausy") and reads[0] > 1

    _read_fails(cluster, second_read_of_pausy)
    rc = cli.main(["apply", str(repo), "--out", str(out), "--pause-wait", "1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pausy: paused in git, but" in err and "NOT enforced" in err
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
    real = FakeCluster._api

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb != "delete":
            return inner

        def delete(name, ns, **kw):
            if name == target:
                raise ApiException(status=status, reason="refused")
            return inner(name, ns, **kw)

        return delete

    cluster._api = method


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
    _unreadable(cluster, "ConfigMap")
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


# --- server-side apply: who owns spec.suspend, who owns the record (C16) --

KUEUE_MANAGER = "kueue-admission"


def _unpause(repo: Path, name: str = "pausy") -> None:
    path = repo / "campaigns" / f"{name}.yaml"
    path.write_text(path.read_text().replace("suspend: true\n", ""))


def test_a_field_manager_owns_what_it_applies_and_conflicts_are_409s(cluster):
    """The fake itself: what the tests below lean on is server-side apply's
    per-manager ownership, so it is held to the rules it claims."""
    job = {"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": "j"}, "spec": {"suspend": True, "x": 1}}  # fmt: skip
    with pytest.raises(ApiException) as e:  # a Job is not created without one
        cluster.server_side_apply(job, "a")
    assert e.value.status == 422
    cluster.server_side_apply({**job, "spec": {"template": 1}}, "b")
    cluster.server_side_apply(job, "a")
    cluster.update("Job", "j", KUEUE_MANAGER, {"spec": {"suspend": False}})
    with pytest.raises(ApiException) as e:
        cluster.server_side_apply(job, "a")
    assert e.value.status == 409 and KUEUE_MANAGER in e.value.body
    cluster.server_side_apply(job, "a", force=True)
    assert cluster.find("Job", "j")["spec"]["suspend"] is True
    # Created without it, a Job has the default, owned by nobody.
    cluster.server_side_apply(
        {**job, "metadata": {"name": "k"}, "spec": {"template": 1}}, "a"
    )
    assert cluster.find("Job", "k")["spec"] == {"template": 1, "suspend": False}
    # A dry run answers what the write would: the same conflict.
    cluster.update("Job", "j", KUEUE_MANAGER, {"spec": {"suspend": False}})
    with pytest.raises(ApiException):
        cluster.server_side_apply(job, "a", dry_run=True)
    cluster.server_side_apply(job, "a", force=True)
    # Released by its last owner: a Job's suspend goes back to its default.
    cluster.server_side_apply({**job, "spec": {"x": 1}}, "a")
    assert cluster.find("Job", "j")["spec"] == {"template": 1, "x": 1, "suspend": False}
    # Set to the same value by two managers, it is theirs jointly.
    cluster.server_side_apply(job, "a")
    cluster.server_side_apply({**job, "spec": {"template": 1, "suspend": True}}, "b")
    cluster.server_side_apply({**job, "spec": {"x": 1}}, "a")
    assert cluster.find("Job", "j")["spec"]["suspend"] is True
    # A Job's pod template is fixed at create: changing it -- forced, as a
    # dry run, or by letting it go -- is a 422 naming the field.
    for kw in ({"force": True}, {"dry_run": True}):
        with pytest.raises(ApiException) as e:
            cluster.server_side_apply({**job, "spec": {"template": 2}}, "b", **kw)
        assert e.value.status == 422
        assert cluster_mod._immutable_fields(e.value) == ("spec.template",)
    with pytest.raises(ApiException):
        cluster.server_side_apply({**job, "spec": {"suspend": True}}, "b")
    assert cluster.find("Job", "j")["spec"]["template"] == 1


def test_unpausing_a_campaign_kueue_suspended_leaves_suspend_to_kueue(
    tmp_path, cluster
):
    """The common path: Kueue admitted the paused campaign in the second it
    was created and suspended it again when the pause sync deactivated its
    Workload, so Kueue owns ``spec.suspend``. The unpausing apply stops
    sending the field and the Job stays suspended until Kueue admits it."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {"uid-pausy": _workload("wl-pausy", True)}
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    cluster.update("Job", "pausy", KUEUE_MANAGER, {"spec": {"suspend": False}})
    cluster.update("Job", "pausy", KUEUE_MANAGER, {"spec": {"suspend": True}})
    _unpause(repo)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.find("Job", "pausy")["spec"]["suspend"] is True
    assert cluster.of("patch")[-1] == ("patch", "wl-pausy", True)


def test_unpausing_a_campaign_kueue_never_admitted_does_not_start_it_unadmitted(
    tmp_path, cluster
):
    """Paused before Kueue ever admitted it -- a full queue, the case a queue
    exists for -- the apply alone owns ``spec.suspend: true``. Dropping the
    field released it, the API server put the default back, and the Job
    controller started the campaign's pods with no admission and no quota:
    Kueue's reconciler stops such a Job again seconds later, mid-volume.
    The field is handed to a manager of its own first, so the Job waits,
    suspended, for Kueue to admit it like any other."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {"uid-pausy": _workload("wl-pausy", True)}
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _unpause(repo)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.find("Job", "pausy")["spec"]["suspend"] is True
    # And admission still works: Kueue's write is not refused by the holder.
    cluster.update("Job", "pausy", KUEUE_MANAGER, {"spec": {"suspend": False}})
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.find("Job", "pausy")["spec"]["suspend"] is False


def test_a_job_gone_before_its_suspend_is_held_is_created_not_refused(
    tmp_path, cluster, capsys
):
    """Deleted between the read and the hand-over, the Job is not there to
    hold anything on: the partial apply would try a create and get a 422,
    which read as the campaign's Job refused. It is created as usual."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {"uid-pausy": _workload("wl-pausy", True)}
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _unpause(repo)
    _during(cluster, "campaign-pausy", lambda: _drop_job(cluster, "pausy"))
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.find("Job", "pausy") is not None
    assert "422" not in capsys.readouterr().err


def test_the_web_and_the_apply_share_the_status_record_by_field(tmp_path, cluster):
    """Two writers, one record: the read API applies it unforced as
    ``htrflow-web`` while the campaign runs, the apply writes the ending it
    reads off the finished Job, forced. The apply's fields win; the sentences
    only the read API knows (``failedVolumes``) survive, since the apply never
    sends them; and the read API's next unforced write of a field the apply
    now owns is a conflict, not an overwrite."""
    from htrflow_web.kube import FIELD_MANAGER as WEB_MANAGER

    record = _status("kyrk", "Running", failedVolumes="R1: fetch failed")
    record["apiVersion"] = "v1"
    cluster.server_side_apply(record, WEB_MANAGER)
    live_job = _object("Job", "kyrk")
    live_job["metadata"]["namespace"] = NS
    live_job["status"] = {
        "conditions": [{"type": "Failed", "status": "True"}],
        "succeeded": 2,
    }
    cluster.live.append(live_job)
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster_mod.FIELD_MANAGER != WEB_MANAGER
    assert cluster.managers["campaign-kyrk-status"] == cluster_mod.FIELD_MANAGER
    assert "failedVolumes" not in cluster.applied["campaign-kyrk-status"]["data"]
    stored = cluster.find("ConfigMap", "campaign-kyrk-status")
    assert stored["data"]["phase"] == "PartiallyFailed"
    assert stored["data"]["failedVolumes"] == "R1: fetch failed"
    with pytest.raises(ApiException) as e:
        cluster.server_side_apply(record, WEB_MANAGER)
    assert e.value.status == 409


# --- one Workload's problem is that campaign's, not the apply's (C-9) ----


def test_a_pause_sync_error_is_one_campaigns_problem(tmp_path, cluster, capsys):
    """A Workload deleted between the list and the patch (404), or a patch
    the Role does not allow (403), used to leave the apply by the outer
    handler: every pause after it unsynced, the prune skipped, no summary.
    It is that campaign's problem now; the rest goes on, and a paused one
    that did not take is still the unenforced pause, exit 1."""
    repo, out = _repo(tmp_path, paused="pausy"), tmp_path / "rendered"
    cluster.workloads = {
        "uid-kyrk": _workload("wl-kyrk", False),
        "uid-loc": _workload("wl-loc", False),
        "uid-pausy": _workload("wl-pausy", True),
    }
    cluster.workload_errors = {"wl-kyrk": 404, "wl-pausy": 403}
    cluster.live = [_object("Job", "cancelled")]
    rc = cli.main(["apply", str(repo), "--out", str(out), "--prune"])
    assert cluster.of("patch") == [("patch", "wl-loc", True)], "the others go on"
    assert ("delete", "Job", "cancelled") in cluster.calls, "so does the prune"
    assert rc == 1, "a pause that did not take outranks everything"
    err = capsys.readouterr().err
    assert "patch Workload/wl-kyrk: 404" in err
    assert "not allowed to patch Workload/wl-pausy" in err
    assert "pausy: paused in git, but" in err
    # Their own line, not the refused-objects summary: a Workload is not an
    # object this apply renders, and nothing here was refused (C-9).
    (line,) = [x for x in err.splitlines() if x.startswith("the pause sync")]
    assert "Job/kyrk, Job/pausy" in line and line.endswith("(exit 1)")
    assert "objects were refused" not in err


def test_an_unpause_that_did_not_take_is_a_change_still_to_make(
    tmp_path, cluster, capsys
):
    """Only an unpaused campaign's Workload failing: nothing is running that
    git says should not be, so it is the refused-object exit, 3."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    cluster.workloads = {"uid-kyrk": _workload("wl-kyrk", False)}
    cluster.workload_errors = {"wl-kyrk": 404}
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert capsys.readouterr().err.splitlines()[-1] == (
        "the pause sync did not reach the Kueue Workload of Job/kyrk; see above "
        "(exit 3)"
    )


# --- the live pipeline ConfigMap is a record too (C-7) --------------------


def _edit_steps(repo: Path) -> None:
    path = repo / "pipelines" / "demo-v1.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["steps"][-1]["settings"]["model"] = "SomethingElse"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_a_steps_edit_under_a_running_campaign_is_refused_by_the_live_record(
    tmp_path, cluster, capsys
):
    """No committed `rendered/` -- a laptop apply of a checkout without one,
    a change that deleted `rendered/pipelines/<id>.yaml` -- and the render
    has nothing to hold a steps edit against. The pipeline ConfigMap is
    mutable, so the edit was applied, and every index not yet started ran a
    different recipe under the same results id. The live ConfigMap is the
    record, held against whenever a live campaign Job still mounts it."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    before = _live(cluster, "htr-pipeline-demo-v1")["data"]["pipeline.yaml"]
    _edit_steps(repo)
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 1
    err = capsys.readouterr().err
    assert "pipeline demo-v1 is in the cluster with different steps" in err
    assert "kyrk, loc" in err and "nothing was applied" in err
    assert cluster.of("apply") == [] and cluster.of("dry-run") == []
    assert _live(cluster, "htr-pipeline-demo-v1")["data"]["pipeline.yaml"] == before


def test_a_size_edit_under_a_running_campaign_is_refused_by_the_live_record(
    tmp_path, cluster, capsys
):
    """The size is part of the recipe (B105), and the live pipeline
    ConfigMap records it: with no `rendered/` to hold the edit against, the
    live record refuses it as it refuses a steps edit."""
    repo = _repo(tmp_path)
    config = repo / "converter.yaml"
    config.write_text(
        config.read_text()
        + "sizes:\n  small: {cpu: 4, memory: 16Gi}\n  large: {cpu: 8, memory: 32Gi}\n"
    )
    _edit(repo / "pipelines" / "demo-v1.yaml", size="small")
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    _edit(repo / "pipelines" / "demo-v1.yaml", size="large")
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 1
    err = capsys.readouterr().err
    assert "pipeline demo-v1 is in the cluster with a different size" in err
    assert cluster.of("apply") == [] and cluster.of("dry-run") == []


def test_a_steps_edit_once_every_campaign_on_it_has_ended_is_applied(tmp_path, cluster):
    """The rule is the render's own: an id is held while a campaign still
    runs it. A Job that has ended runs nothing more, and a warm-up is not a
    campaign."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    for name in ("kyrk", "loc"):
        _live(cluster, name)["status"] = {
            "conditions": [{"type": "Complete", "status": "True"}]
        }
    _edit_steps(repo)
    for name in ("kyrk", "loc"):  # the finished campaigns leave git
        (repo / "campaigns" / f"{name}.yaml").unlink()
    (repo / "campaigns" / "fresh.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R7777777\n"
    )
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 0
    assert (
        "SomethingElse"
        in _live(cluster, "htr-pipeline-demo-v1")["data"]["pipeline.yaml"]
    )


# --- a finished record counts only when it names apply's own Job (S-11) ---

JOB_UID = "htrflow.riksarkivet.se/job-uid"


def _web_writes(cluster, name: str, phase: str, job_uid: str) -> None:
    """The read API's write -- or anything that holds its ServiceAccount:
    the chart lets it write every `campaign-<x>-status` name."""
    from htrflow_web.kube import FIELD_MANAGER as WEB_MANAGER

    record = _status(name, phase, jobUid=job_uid)
    record["apiVersion"] = "v1"
    cluster.server_side_apply(record, WEB_MANAGER, force=True)


def _drop_job(cluster, name: str) -> None:
    cluster.live = [
        o
        for o in cluster.live
        if not (o["kind"] == "Job" and o["metadata"]["name"] == name)
    ]


def test_the_campaign_record_names_the_job_the_apply_created(tmp_path, cluster):
    """The campaign ConfigMap is written by the apply identity alone (the
    read API may write only `-status` names), so it is where the apply
    keeps which Job it made -- the uid a record has to name to be believed."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    record = _live(cluster, "campaign-kyrk")
    assert (
        record["metadata"]["annotations"][JOB_UID]
        == _live(cluster, "kyrk")["metadata"]["uid"]
    )


def test_a_finished_record_naming_another_job_is_not_believed(
    tmp_path, cluster, capsys
):
    """A status record whose Job is gone says how the campaign ended, and the
    apply leaves a finished one alone. Anything holding the read API's
    ServiceAccount can write that record, for any campaign: a `Succeeded`
    beside a Job removed by hand kept the campaign from ever running again.
    The record has to name the Job the apply itself created."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _drop_job(cluster, "kyrk")
    _web_writes(cluster, "kyrk", "Succeeded", "uid-somebody-elses")
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") in cluster.calls
    captured = capsys.readouterr()
    assert "left alone" not in captured.out
    assert "not the Job this apply created" in captured.err


def test_a_finished_record_naming_the_applys_job_is_believed(tmp_path, cluster):
    """The read API's own legitimate record -- the case it exists for: the
    campaign finished while the status page was open and the TTL reaped
    the Job before the next apply."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    uid = _live(cluster, "kyrk")["metadata"]["uid"]
    _drop_job(cluster, "kyrk")
    _web_writes(cluster, "kyrk", "Succeeded", uid)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") not in cluster.calls


def test_a_record_of_the_job_before_a_recreated_one_is_not_believed(
    tmp_path, cluster, capsys
):
    """A Job deleted mid-run is created again by the next apply, under the
    same name and a new uid. A finished record the read API wrote about the
    OLD Job says nothing about the new one: once the new Job is reaped, that
    record must not keep the campaign from running."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    old = _live(cluster, "kyrk")["metadata"]["uid"]
    _drop_job(cluster, "kyrk")
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    new = _live(cluster, "kyrk")["metadata"]["uid"]
    assert new != old, "the fake hands out a fresh uid, as the API server does"
    record = _live(cluster, "campaign-kyrk")
    assert record["metadata"]["annotations"][JOB_UID] == new
    _drop_job(cluster, "kyrk")
    _web_writes(cluster, "kyrk", "Succeeded", old)
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") in cluster.calls
    assert "not the Job this apply created" in capsys.readouterr().err
    # And the new Job's own record is believed.
    newest = _live(cluster, "kyrk")["metadata"]["uid"]
    _drop_job(cluster, "kyrk")
    _web_writes(cluster, "kyrk", "Succeeded", newest)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") not in cluster.calls


def test_a_record_for_a_job_that_was_never_created_is_not_believed(tmp_path, cluster):
    """The pair came apart: the ConfigMap went out and its Job did not. No
    Job exists for any record to be about, so none is believed."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    real = FakeCluster._api

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb == "patch" and kind == "Job" and name == "kyrk":

            def patch(name, ns, obj, **kw):
                if not kw.get("dry_run"):
                    raise cluster_mod.ClusterError("apply Job/kyrk: 500")
                return inner(name, ns, obj, **kw)

            return patch
        return inner

    cluster._api = method
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert _live(cluster, "campaign-kyrk")["metadata"]["annotations"][JOB_UID] == ""
    _web_writes(cluster, "kyrk", "Succeeded", "uid-anything")
    cluster._api = lambda kind, verb, name="": real(cluster, kind, verb, name)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") in cluster.calls


def test_a_refused_uid_write_is_repaired_by_the_next_apply(tmp_path, cluster, capsys):
    """The second write of a new campaign's ConfigMap only records which Job
    it runs. Refused, the campaign still runs -- and the next apply, which
    finds the Job finished and leaves the campaign alone, still sends the
    missing uid. Without it the finished campaign's record would not be
    believed once the Job is reaped, and the whole campaign would run
    again."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    real = FakeCluster._api
    sent = [0]

    def method(kind, verb, name=""):
        inner = real(cluster, kind, verb, name)
        if verb == "patch" and name == "campaign-kyrk":

            def patch(name, ns, obj, **kw):
                sent[0] += 1
                if sent[0] == 2:
                    raise cluster_mod.ClusterError("apply ConfigMap/campaign-kyrk: 500")
                return inner(name, ns, obj, **kw)

            return patch
        return inner

    cluster._api = method
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert "could not record which Job campaign kyrk runs" in capsys.readouterr().err
    assert _live(cluster, "campaign-kyrk")["metadata"]["annotations"][JOB_UID] == ""
    job = _live(cluster, "kyrk")
    job["metadata"]["namespace"] = NS
    job["status"] = {
        "conditions": [{"type": "Complete", "status": "True"}],
        "succeeded": job["spec"]["completions"],
    }
    applied_at = "htrflow.riksarkivet.se/applied-at"
    before = _live(cluster, "campaign-kyrk")["metadata"]["annotations"][applied_at]
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    record = _live(cluster, "campaign-kyrk")
    assert record["metadata"]["annotations"][JOB_UID] == job["metadata"]["uid"]
    assert record["metadata"]["annotations"][applied_at] == before, "not re-applied"
    _drop_job(cluster, "kyrk")
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "kyrk") not in cluster.calls, "the record is believed"


# --- the namespace the caller meant, said out loud (S-7) ------------------


def test_a_namespace_that_is_not_the_repos_is_refused_before_the_cluster(
    tmp_path, cluster, capsys
):
    """The target namespace comes from the campaigns repo's converter.yaml,
    while every policy the chart ships matches the release namespace alone.
    From a kubeconfig with wider rights than the chart's Role, a repo saying
    `namespace: other` put Jobs where no policy applies. The caller says
    which namespace it means, and a repo that disagrees is refused."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    rc = cli.main(["apply", str(repo), "--out", str(out), "--namespace", "other"])
    assert rc == 1
    assert cluster.calls == [] and cluster.namespace == ""
    err = capsys.readouterr().err
    assert "namespace: htr-test" in err and "--namespace other" in err


def test_the_namespace_the_repo_names_is_applied(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out), "--namespace", NS]) == 0
    assert cluster.namespace == NS


def test_a_dry_run_checks_the_namespace_too(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    argv = ["apply", str(repo), "--out", str(out), "--dry-run", "--namespace", "x"]
    assert cli.main(argv) == 1


# --- one apply at a time (C-12) -------------------------------------------


def _lease(holder: str, renewed: str, seconds: int = 300) -> dict:
    return {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": {"name": "htrflow-campaigns-apply", "resourceVersion": "7"},
        "spec": {
            "holderIdentity": holder,
            "leaseDurationSeconds": seconds,
            "acquireTime": renewed,
            "renewTime": renewed,
        },
    }


def _ago(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_an_apply_already_running_is_waited_for_not_raced(tmp_path, cluster, capsys):
    """The Argo CD hook and a hand-run apply at once: the older checkout's
    prune deleted the Job and ConfigMap the newer one had just created. The
    second apply sees the first one's Lease and sends nothing."""
    cluster.leases["htrflow-campaigns-apply"] = _lease("hook-pod/1", _ago(10))
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1
    assert cluster.of("apply") == [] and cluster.of("delete") == []
    err = capsys.readouterr().err
    assert "another htrflow-campaigns apply is running in htr-test" in err
    assert "hook-pod/1" in err and "nothing was applied" in err


def test_the_lease_is_held_for_the_run_and_released_after_it(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    held, released = cluster.lease_log
    assert held and released is None
    assert cluster.leases["htrflow-campaigns-apply"]["spec"]["holderIdentity"] is None
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0, "free again"


def test_a_lease_its_holder_stopped_renewing_is_taken_over(tmp_path, cluster):
    """A holder SIGKILLed mid-apply never releases. Past its duration the
    Lease is a dead holder's, and the next apply goes ahead."""
    cluster.leases["htrflow-campaigns-apply"] = _lease("dead-pod/1", _ago(600))
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.of("apply")


def test_the_lease_is_released_when_the_apply_fails(tmp_path, cluster):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    _refuses(cluster, "kyrk", cluster_mod.ClusterError("apply Job/kyrk: 409"))
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert cluster.leases["htrflow-campaigns-apply"]["spec"]["holderIdentity"] is None


def _during(cluster, name: str, act) -> None:
    """Run ``act()`` as the apply first sends object ``name`` -- once: a new
    campaign's ConfigMap is sent again after its Job, and one event is not
    two."""
    real = FakeCluster._api
    left = [1]

    def api(kind, verb, n=""):
        if verb == "patch" and n == name and left[0]:
            left[0] -= 1
            act()
        return real(cluster, kind, verb, n)

    cluster._api = api


def test_an_apply_whose_lease_was_taken_over_stops(
    tmp_path, cluster, monkeypatch, capsys
):
    """Renewed before every request; a renewal that finds the Lease held by
    someone else is two applies running at once, and this one stops there,
    naming the new holder -- not as a server that stopped answering."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    monkeypatch.setattr(cluster_mod, "LEASE_RENEW", -1)  # renew every request
    lease = "htrflow-campaigns-apply"

    def take_over():
        cluster.leases[lease]["metadata"]["resourceVersion"] = "x"
        cluster.leases[lease]["spec"]["holderIdentity"] = "other-pod/9"

    _during(cluster, "campaign-kyrk", take_over)
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1
    assert ("apply", "Job", "kyrk") not in cluster.calls
    assert cluster.of("delete") == []
    err = capsys.readouterr().err
    assert "was taken over by other-pod/9" in err
    assert "stopped answering" not in err and "cannot reach" not in err
    assert cluster.leases[lease]["spec"]["holderIdentity"] == "other-pod/9"


@pytest.mark.parametrize(("status", "why"), [(403, "Forbidden"), (404, "deleted")])
def test_a_renewal_the_server_refuses_stops_the_apply(
    tmp_path, cluster, monkeypatch, capsys, status, why
):
    """A renewal refused (no `update` granted) or answered 404 (the Lease
    deleted) is a Lease no longer held. It used to be caught as one object's
    refusal, and the apply ran on without the Lease, prune and all."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    monkeypatch.setattr(cluster_mod, "LEASE_RENEW", -1)
    cluster.live = [_object("Job", "cancelled")]

    def refuse():
        if status == 404:
            cluster.leases.clear()
        else:
            cluster.lease_errors["write"] = status

    _during(cluster, "campaign-kyrk", refuse)
    assert cli.main(["apply", str(repo), "--out", str(out), "--prune"]) == 1
    assert ("apply", "Job", "kyrk") not in cluster.calls
    assert cluster.of("delete") == [], "no prune without the Lease"
    err = capsys.readouterr().err
    assert "could not renew Lease/htrflow-campaigns-apply, so stopped" in err
    assert "objects were refused" not in err


def test_an_apply_that_could_not_renew_in_time_stops_on_its_own(
    tmp_path, cluster, monkeypatch, capsys
):
    """One long request is a gap in the renewals. Past the local deadline the
    Lease may already be another apply's, so this one stops rather than go
    on deleting beside it."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    clock = [1000.0]
    monkeypatch.setattr(cluster_mod.time, "monotonic", lambda: clock[0])

    def stall():
        clock[0] += cluster_mod.LEASE_SECONDS

    _during(cluster, "campaign-kyrk", stall)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert ("apply", "Job", "kyrk") not in cluster.calls
    assert "went unrenewed" in capsys.readouterr().err


def test_lease_expiry_is_judged_on_the_api_servers_clock(tmp_path, cluster):
    """A laptop whose clock runs ten minutes fast saw a live hook's Lease as
    long expired and took it over. Both sides of the comparison are the API
    server's clock, from its Date header."""
    server = datetime.now(timezone.utc) - timedelta(minutes=15)
    cluster.server_time = server
    renewed = (server - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cluster.leases["htrflow-campaigns-apply"] = _lease("hook-pod/1", renewed)
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 1
    assert cluster.of("apply") == []
    # And what this apply writes is the server's time too.
    cluster.leases.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    written = cluster.leases["htrflow-campaigns-apply"]["spec"]["acquireTime"]
    assert written.startswith(server.strftime("%Y-%m-%dT%H:%M"))


def test_a_release_whose_last_renewal_answer_was_lost_still_releases(
    tmp_path, cluster, monkeypatch
):
    """A renewal that landed but whose answer was lost leaves this apply one
    resourceVersion behind: its release got a 409, swallowed, and the Lease
    stayed held for its whole duration. The 409 is re-read: still this
    apply's, so it is released on the fresh version."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    lease = "htrflow-campaigns-apply"

    def lost_answer():
        cluster.leases[lease]["metadata"]["resourceVersion"] = "moved-on"

    _during(cluster, "loc", lost_answer)
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert cluster.leases[lease]["spec"]["holderIdentity"] is None


def test_sigterm_releases_the_lease(tmp_path, cluster):
    """Argo CD terminates a hook with SIGTERM, and Python's default action
    skips every `finally`: the Lease stayed held, and the next hook
    (backoffLimit 0) failed on it. The apply turns SIGTERM into an exit
    that unwinds, 143 as the shell reports it.

    A handler of the test's own stands in for the default while it runs:
    without it, an apply that installed nothing would let the test's own
    signal end pytest itself, and every test after this one with it."""
    import os
    import signal

    repo, out = _repo(tmp_path), tmp_path / "rendered"
    caught: list[int] = []
    installed: list[object] = []

    def sentinel(signum, frame):
        caught.append(signum)

    def terminate():
        installed.append(signal.getsignal(signal.SIGTERM))
        os.kill(os.getpid(), signal.SIGTERM)

    before = signal.signal(signal.SIGTERM, sentinel)
    try:
        _during(cluster, "campaign-kyrk", terminate)
        with pytest.raises(SystemExit) as e:
            cli.main(["apply", str(repo), "--out", str(out)])
        assert installed == [cli._terminated], "the apply's handler was in place"
        assert caught == []
        assert e.value.code == 143
        lease = cluster.leases["htrflow-campaigns-apply"]
        assert lease["spec"]["holderIdentity"] is None
        assert signal.getsignal(signal.SIGTERM) is sentinel, "the handler is put back"
    finally:
        signal.signal(signal.SIGTERM, before)


# --- a window change under a running campaign, held against the cluster --


def test_a_window_change_under_a_running_campaign_is_refused_by_the_live_job(
    tmp_path, cluster, capsys
):
    """Kueue stops every pod of an admitted Job whose pod count --
    min(parallelism, completions) -- no longer matches its Workload, and
    queues the campaign again. `validate` holds that against `rendered/`,
    which can lag the cluster; the live Job is the record apply holds it
    against, before anything is sent."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    _edit(repo / "campaigns" / "kyrk.yaml", window=1)
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 1
    err = capsys.readouterr().err
    assert "campaign kyrk runs 3 pods at a time and would now run 1" in err
    assert "pause the campaign first" in err
    assert cluster.of("apply") == [] and cluster.of("dry-run") == []


def test_a_window_change_under_a_paused_campaign_is_applied(tmp_path, cluster):
    """The way the sentence says: a suspended Job holds no quota, and Kueue
    updates its Workload in place."""
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "one")]) == 0
    _live(cluster, "kyrk")["spec"]["suspend"] = True
    _edit(repo / "campaigns" / "kyrk.yaml", window=1)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "two")]) == 0
    assert ("dry-run", "Job", "kyrk") in cluster.calls


# --- two running campaigns on one pipeline never share a volume (C-13) ---


def _rerun(repo: Path, name: str = "rerun", *volumes: str) -> None:
    listed = "".join(f"  - {v}\n" for v in volumes or ("R0001203", "R4444444"))
    (repo / "campaigns" / f"{name}.yaml").write_text(
        f"pipeline: demo-v1\nvolumes:\n{listed}"
    )


def test_a_volume_a_running_campaign_on_the_pipeline_has_is_refused(
    tmp_path, cluster, capsys
):
    """Results are keyed `<pipeline>/<volume>/`, so two campaigns running one
    volume on one pipeline race on the same progress and manifest objects.
    `validate` cannot see what is running; the apply can, and refuses the
    newcomer's pair -- the other campaigns go out as usual."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _rerun(repo)
    cluster.calls.clear()
    capsys.readouterr()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    applied = [c[2] for c in cluster.of("apply")]
    assert "rerun" not in applied and "campaign-rerun" not in applied
    assert "kyrk" in applied
    err = capsys.readouterr().err
    assert "campaign rerun shares volume R0001203 with campaign kyrk" in err
    assert "Job/rerun" in err.splitlines()[-1]


def test_a_rerun_after_the_old_campaign_ended_is_applied(tmp_path, cluster):
    """The documented way to run a failed volume again is a new campaign on
    the same pipeline, once the old one is over."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _live(cluster, "kyrk")["status"] = {
        "conditions": [{"type": "Failed", "status": "True"}]
    }
    _rerun(repo)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    assert ("apply", "Job", "rerun") in cluster.calls


def test_two_new_campaigns_sharing_a_volume_are_not_both_started(
    tmp_path, cluster, capsys
):
    """Neither is running yet, and both would be by the end of this apply:
    the first in the render goes out, the second is refused."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    _rerun(repo, "zz-second", *[f"R00{n}" for n in range(10, 16)], "R0001203")
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert ("apply", "Job", "kyrk") in cluster.calls
    assert ("apply", "Job", "zz-second") not in cluster.calls
    err = capsys.readouterr().err
    assert "campaign zz-second shares volume R0001203 with campaign kyrk" in err


def test_many_shared_volumes_are_named_first_few_then_counted(
    tmp_path, cluster, capsys
):
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    _rerun(repo, "zz-all", "R0001203", "R0009999", "R0009998", "dodsbok-1698",
           "loose-scans")  # fmt: skip
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert "and 2 more" in capsys.readouterr().err


def test_a_running_campaigns_volumes_it_may_not_read_hold_the_newcomers(
    tmp_path, cluster, capsys
):
    """A check that cannot be made is not one that passed: the campaigns it
    was for are left as they were, and the pipelines still go out."""
    repo, out = _repo(tmp_path), tmp_path / "rendered"
    assert cli.main(["apply", str(repo), "--out", str(out)]) == 0
    _rerun(repo)
    reads = [0]

    def second_read_of_loc(kind, name):
        if name == "campaign-loc":
            reads[0] += 1
        return name == "campaign-loc" and reads[0] > 1  # the first: append-only

    _read_fails(cluster, second_read_of_loc)
    cluster.calls.clear()
    assert cli.main(["apply", str(repo), "--out", str(out)]) == cli.REFUSED
    assert ("apply", "Job", "rerun") not in cluster.calls
    assert ("apply", "ConfigMap", "htr-pipeline-demo-v1") in cluster.calls
    assert "could not tell whether campaign rerun shares a volume" in (
        capsys.readouterr().err
    )


# --- a size's flavor against the cluster's (review of PR #35, item 3) -----

SIZED = """
flavors:
  - name: small-gpu
    nodeLabels: {nvidia.com/gpu.product: NVIDIA-L4}
  - name: large-gpu
    nodeLabels: {nvidia.com/gpu.product: NVIDIA-A100}
sizes:
  large: {flavor: large-gpu, cpu: 8, memory: 32Gi}
"""


def _sized_repo(tmp_path) -> Path:
    repo = _repo(tmp_path)
    config = repo / "converter.yaml"
    config.write_text(config.read_text() + SIZED)
    _edit(repo / "pipelines" / "demo-v1.yaml", size="large")
    return repo


def _queue(cluster, **labels: dict) -> None:
    """The LocalQueue converter.yaml names, its ClusterQueue, and one
    ResourceFlavor per keyword, in that order."""
    kueue = cluster.kueue_objects
    kueue[("localqueues", "htr-test")] = {"spec": {"clusterQueue": "team-cq"}}
    flavors = [{"name": name.replace("_", "-")} for name in labels]
    kueue[("clusterqueues", "team-cq")] = {
        "spec": {"resourceGroups": [{"flavors": flavors}]}
    }
    for name, node_labels in labels.items():
        kueue[("resourceflavors", name.replace("_", "-"))] = {
            "spec": {"nodeLabels": node_labels}
        }


def test_a_sized_campaign_goes_out_when_its_flavors_are_the_clusters(tmp_path, cluster):
    repo = _sized_repo(tmp_path)
    _queue(
        cluster,
        small_gpu={"nvidia.com/gpu.product": "NVIDIA-L4"},
        large_gpu={"nvidia.com/gpu.product": "NVIDIA-A100"},
    )
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    assert ("get", "resourceflavors", "large-gpu") in cluster.calls
    assert _live(cluster, "kyrk")


def test_a_sized_campaign_is_held_when_the_clusters_flavors_differ(
    tmp_path, cluster, capsys
):
    """A label value converter.yaml spells otherwise is a campaign Kueue
    never admits; a flavor it does not list is one a sized pod may be
    admitted on and never scheduled. Either holds the campaign back."""
    repo = _sized_repo(tmp_path)
    _queue(
        cluster,
        small_gpu={"nvidia.com/gpu.product": "NVIDIA-L4"},
        large_gpu={"nvidia.com/gpu.product": "NVIDIA-A100-SXM4-80GB"},
        spare_gpu={"pool": "spare"},
    )
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == (
        cli.REFUSED
    )
    err = capsys.readouterr().err
    assert (
        "campaign kyrk runs at size large, on flavor large-gpu, and converter.yaml's"
        " flavors are not ClusterQueue team-cq's: flavor large-gpu is"
        " nvidia.com/gpu.product=NVIDIA-A100-SXM4-80GB in the cluster and"
        " nvidia.com/gpu.product=NVIDIA-A100 in converter.yaml; the cluster has"
        " flavor spare-gpu, which converter.yaml does not list" in err
    )
    assert not any(o["metadata"]["name"] == "kyrk" for o in cluster.live)
    assert _live(cluster, "htr-pipeline-demo-v1")


def test_without_the_right_to_read_the_flavors_the_apply_warns_and_goes_on(
    tmp_path, cluster, capsys
):
    """A laptop kubeconfig may not read cluster-scoped Kueue objects; the
    chart's apply identity may."""
    repo = _sized_repo(tmp_path)
    _queue(cluster, small_gpu={"x": "1"})
    cluster.kueue_errors = {"clusterqueues": 403}
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    assert "applied unchecked" in capsys.readouterr().err
    assert _live(cluster, "kyrk")


def test_an_apply_with_no_flavored_size_reads_no_queue(tmp_path, cluster):
    repo = _repo(tmp_path)
    assert cli.main(["apply", str(repo), "--out", str(tmp_path / "out")]) == 0
    assert not [c for c in cluster.calls if c[0] == "get"]
