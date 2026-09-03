"""The API server, as ``htrflow-campaigns apply`` needs it: server-side
apply, prune, and the Kueue pause sync that makes ``suspend:`` in git hold.

Not ``kubectl`` as a subprocess: same library and auth path as
``packages/web``, no binary to put on a CI image's ``PATH``, exceptions
instead of exit codes -- and a prune we own, rather than ``kubectl apply
--prune``'s client-side sweep, whose semantics have been "deprecated,
replaced by an alpha flag" for several releases now.

``_preload_content=False`` everywhere, so what comes back is the plain JSON
the API server sent -- the shape the rendered manifests are in.
"""

from __future__ import annotations

import json
import time
from typing import Any

from kubernetes import client, config

from .render import CAMPAIGN_SELECTOR

#: Field manager for every apply: what lets a field this tool stopped
#: rendering be removed from a live object -- the role `kubectl`'s
#: `last-applied-configuration` annotation played, kept by the API server.
FIELD_MANAGER = "htrflow-campaigns"
APPLY_PATCH = "application/apply-patch+yaml"
MERGE_PATCH = "application/merge-patch+json"

_KUEUE = ("kueue.x-k8s.io", "v1beta1")
_WORKLOADS = "workloads"
_JOB_UID_LABEL = "kueue.x-k8s.io/job-uid"
#: The two kinds this tool renders -> (client attribute, method noun).
_KINDS = {"Job": ("batch", "job"), "ConfigMap": ("core", "config_map")}


def _raw(fn: Any, *args: Any, **kwargs: Any) -> dict:
    return json.loads(fn(*args, _preload_content=False, **kwargs).data)


class Cluster:
    """One namespace, reached through the kubeconfig or the pod's own token."""

    def __init__(self, namespace: str) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.namespace = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

    def _method(self, kind: str, verb: str) -> Any:
        api, noun = _KINDS[kind]
        return getattr(getattr(self, api), f"{verb}_namespaced_{noun}")

    def apply(self, obj: dict) -> dict:
        """Server-side apply ``obj``; returns what the server stored.

        ``force=True`` takes the fields back from whatever manager owns them
        -- a Job applied by `kubectl` before this change, a hand edit --
        which is the "git is the truth" rule the campaigns repo runs on.
        """
        return _raw(
            self._method(obj["kind"], "patch"),
            obj["metadata"]["name"],
            obj["metadata"].get("namespace", self.namespace),
            obj,
            field_manager=FIELD_MANAGER,
            force=True,
            _content_type=APPLY_PATCH,
        )

    def prune(self, rendered: set[tuple[str, str]]) -> None:
        """Delete every labelled Job/ConfigMap not in ``rendered``.

        ``rendered`` is ``(kind, name)`` for **all** rendered objects,
        pipelines included: pruning against the campaigns alone would delete
        the pipeline ConfigMaps and warm-up Jobs the same apply just wrote.
        """
        for kind in _KINDS:
            listed = _raw(
                self._method(kind, "list"),
                self.namespace,
                label_selector=CAMPAIGN_SELECTOR,
            )
            for item in listed.get("items", []):
                name = item["metadata"]["name"]
                if (kind, name) in rendered:
                    continue
                extra = {"propagation_policy": "Background"} if kind == "Job" else {}
                self._method(kind, "delete")(name, self.namespace, **extra)
                print(f"pruned: {kind}/{name}")

    def _workload(self, uid: str) -> dict | None:
        """The Kueue Workload of the Job with ``uid``. Kueue labels it with
        that uid, the only link that survives a delete/recreate of the Job."""
        listed = self.custom.list_namespaced_custom_object(
            *_KUEUE,
            self.namespace,
            _WORKLOADS,
            label_selector=f"{_JOB_UID_LABEL}={uid}",
        )
        items = listed.get("items", [])
        return items[0] if items else None

    def sync_pause(self, job: dict, suspended: bool, wait: int) -> int:
        """Put ``suspended`` on the Job's Workload. Non-zero when it cannot.

        Kueue OWNS ``spec.suspend`` for a Workload it has admitted and undoes
        it within seconds; ``spec.active`` is the lever that holds. A
        Workload that already agrees is left alone -- which is what makes a
        re-apply of an unchanged repo issue no patch at all.

        A Workload appears a moment AFTER its Job, and for a paused campaign
        that moment is exactly the window in which Kueue would admit and
        start it -- so a paused campaign waits and then fails loudly. One
        that is not paused needs no wait: a Workload that does not exist is
        not admitted either. (docs/reference/campaign-yaml.md#pausing)
        """
        name, uid = job["metadata"]["name"], job["metadata"]["uid"]
        wl = self._workload(uid)
        for _ in range(wait if wl is None and suspended else 0):
            time.sleep(1)
            wl = self._workload(uid)
            if wl is not None:
                break
        if wl is None:
            if not suspended:
                print(f"{name}: no Workload yet, skipping")
                return 0
            print(
                f"{name}: paused in git, but no Kueue Workload appeared within "
                f"{wait}s — the pause is NOT enforced; re-run the apply"
            )
            return 1
        want = not suspended
        if wl.get("spec", {}).get("active", True) == want:
            return 0
        wl_name = wl["metadata"]["name"]
        print(f"{name}: workload/{wl_name} active={str(want).lower()}")
        self.custom.patch_namespaced_custom_object(
            *_KUEUE,
            self.namespace,
            _WORKLOADS,
            wl_name,
            {"spec": {"active": want}},
            _content_type=MERGE_PATCH,
        )
        return 0
