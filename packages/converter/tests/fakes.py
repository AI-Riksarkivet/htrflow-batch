"""``FakeCluster``: the real ``Cluster`` with dictionaries where the API
server was, shared by every test that drives the converter against a
cluster (``test_apply.py``, ``test_retry_build.py``).

Not a ``conftest.py`` fixture: tests subclass it and reach into its
methods, so it is imported by name; ``conftest.py`` puts this directory on
``sys.path`` for that.
"""

from __future__ import annotations

import copy
import itertools
import json
from datetime import datetime
from email.utils import format_datetime

from kubernetes.client.exceptions import ApiException

from htrflow_converter.cluster import Cluster


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
