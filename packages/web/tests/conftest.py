"""A server-side-apply-faithful store of status ConfigMaps, for the tests
that are about who owns which field of the record.

The recording fakes in ``test_app.py`` keep what was sent and which manager
sent it, and nothing else -- so nothing tested what the API server then
DOES with it, which is where the record's field ownership lives (2026-09-23
review). This models it over what the status ConfigMap uses, its ``data``
keys and its labels, the way the converter's ``FakeCluster`` does for the
objects `apply` writes: each manager owns the fields it last applied; an
apply that would change a field another manager owns is a 409 unless forced,
and forced takes it over; two managers setting the same value own it
jointly; a field a manager stops sending is released, and removed once
nobody owns it. ``metadata.uid`` and ``metadata.resourceVersion`` in an
apply are preconditions, and a uid never creates an object.
"""

from __future__ import annotations

import copy

import pytest
from kubernetes import client


def _conflict(why: str) -> client.ApiException:
    return client.ApiException(status=409, reason=f"Conflict: {why}")


class SsaConfigMaps:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        #: name -> manager -> the fields it owns, as ("data"|"labels", key)
        self.owners: dict[str, dict[str, set[tuple[str, str]]]] = {}
        self._serial = 0

    def _next(self) -> str:
        self._serial += 1
        return str(self._serial)

    def get(self, name: str) -> dict | None:
        obj = self.objects.get(name)
        if obj is None:
            return None
        obj = copy.deepcopy(obj)
        obj["metadata"]["managedFields"] = [
            {
                "manager": manager,
                "operation": "Apply",
                "fieldsV1": {
                    "f:data": {f"f:{k}": {} for kind, k in paths if kind == "data"},
                    "f:metadata": {
                        "f:labels": {
                            f"f:{k}": {} for kind, k in paths if kind == "labels"
                        }
                    },
                },
            }
            for manager, paths in self.owners[name].items()
            if paths
        ]
        return obj

    def delete(self, name: str) -> None:
        """What `htrflow-campaigns apply --prune` does to the record."""
        self.objects.pop(name, None)
        self.owners.pop(name, None)

    def apply(self, body: dict, force: bool = False, manager: str = "") -> str:
        """A server-side apply of ``body`` as ``manager``: the stored uid."""
        meta = body["metadata"]
        name = meta["name"]
        current = self.objects.get(name)
        if "uid" in meta and (
            current is None or current["metadata"]["uid"] != meta["uid"]
        ):
            raise _conflict("uid precondition")
        rv = meta.get("resourceVersion")
        if rv and (current is None or current["metadata"]["resourceVersion"] != rv):
            raise _conflict("resourceVersion precondition")
        stored = copy.deepcopy(current) or {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": meta["namespace"],
                "uid": f"uid-cm-{self._next()}",
                "labels": {},
            },
            "data": {},
        }
        owners = copy.deepcopy(self.owners.get(name, {}))
        sent = {("data", k): v for k, v in (body.get("data") or {}).items()}
        sent |= {("labels", k): v for k, v in (meta.get("labels") or {}).items()}
        values = {("data", k): v for k, v in stored["data"].items()}
        values |= {("labels", k): v for k, v in stored["metadata"]["labels"].items()}
        conflicts = [
            (path, other)
            for path, value in sent.items()
            for other, paths in owners.items()
            if other != manager and path in paths and values.get(path) != value
        ]
        if conflicts and not force:
            raise _conflict(", ".join(f"{m}: {p}" for p, m in conflicts))
        for path, other in conflicts:
            owners[other].discard(path)
        for path in owners.get(manager, set()) - set(sent):
            if not any(path in p for m, p in owners.items() if m != manager):
                values.pop(path, None)
        values |= sent
        owners[manager] = set(sent)
        stored["data"] = {k: v for (kind, k), v in values.items() if kind == "data"}
        stored["metadata"]["labels"] = {
            k: v for (kind, k), v in values.items() if kind == "labels"
        }
        stored["metadata"]["resourceVersion"] = self._next()
        self.objects[name] = stored
        self.owners[name] = owners
        return stored["metadata"]["uid"]

    def owner_of(self, name: str, key: str) -> set[str]:
        return {m for m, p in self.owners.get(name, {}).items() if ("data", key) in p}


@pytest.fixture
def ssa() -> SsaConfigMaps:
    return SsaConfigMaps()
