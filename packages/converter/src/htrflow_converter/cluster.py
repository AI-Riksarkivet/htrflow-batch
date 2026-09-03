"""The API server, as ``htrflow-campaigns apply`` needs it.

Three operations, no more: **server-side apply** of a rendered object,
**prune** of the objects a deleted campaign file left behind, and the
**Kueue pause sync** that makes ``suspend:`` in git hold on the cluster.

Why the official client and not ``kubectl`` as a subprocess: the same
library (and the same in-cluster/kubeconfig auth path) that ``packages/web``
already uses, no binary to put on a CI image's ``PATH``, real exceptions
instead of exit codes, and -- the substantive one -- a **prune we own**.
``kubectl apply --prune`` is a client-side sweep whose semantics have been
"deprecated, replaced by an alpha flag" for several releases; here it is
twelve lines that list by this renderer's own label and delete what this
render did not produce.

Every call goes through ``_preload_content=False``, so what comes back is
the plain JSON dict the API server sent (camelCase, no typed-model round
trip) -- the same shape the rendered manifests are in.
"""

from __future__ import annotations

import json
import time
from typing import Any

from kubernetes import client, config

from .render import CAMPAIGN_SELECTOR

#: Field manager for every apply. It is what makes a second apply of an
#: unchanged repo a no-op and what lets a field this tool stopped rendering
#: be removed from a live object -- the same role `kubectl`'s
#: `last-applied-configuration` annotation played, kept by the API server.
FIELD_MANAGER = "htrflow-campaigns"
APPLY_PATCH = "application/apply-patch+yaml"
MERGE_PATCH = "application/merge-patch+json"

_KUEUE = ("kueue.x-k8s.io", "v1beta1")
_WORKLOADS = "workloads"
_JOB_UID_LABEL = "kueue.x-k8s.io/job-uid"


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

    def _kinds(self) -> dict[str, tuple[Any, Any, Any]]:
        """The two kinds this tool renders: (patch, list, delete) per kind."""
        return {
            "Job": (
                self.batch.patch_namespaced_job,
                self.batch.list_namespaced_job,
                self.batch.delete_namespaced_job,
            ),
            "ConfigMap": (
                self.core.patch_namespaced_config_map,
                self.core.list_namespaced_config_map,
                self.core.delete_namespaced_config_map,
            ),
        }

    def apply(self, obj: dict) -> dict:
        """Server-side apply ``obj``; returns what the server stored.

        A PATCH with the apply content type creates the object when it is
        absent and reconciles it when it is not. ``force=True`` takes the
        fields back from whatever manager owns them -- a Job applied by
        `kubectl` before this change, or a hand edit -- which is exactly the
        "git is the truth" rule the campaigns repo runs on.
        """
        patch = self._kinds()[obj["kind"]][0]
        return _raw(
            patch,
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
        the pipeline ConfigMaps and warm-up Jobs that the same apply just
        wrote. A Job is deleted in the background so its pods go with it.
        """
        for kind, (_, list_, delete) in self._kinds().items():
            body = _raw(list_, self.namespace, label_selector=CAMPAIGN_SELECTOR)
            for item in body.get("items", []):
                name = item["metadata"]["name"]
                if (kind, name) in rendered:
                    continue
                extra = {"propagation_policy": "Background"} if kind == "Job" else {}
                delete(name, self.namespace, **extra)
                print(f"pruned: {kind}/{name}")

    def _workload(self, uid: str) -> dict | None:
        """The Kueue Workload of the Job with ``uid``. Kueue labels it with
        that uid, the only link that survives a delete/recreate of the Job."""
        body = self.custom.list_namespaced_custom_object(
            *_KUEUE,
            self.namespace,
            _WORKLOADS,
            label_selector=f"{_JOB_UID_LABEL}={uid}",
        )
        items = body.get("items", [])
        return items[0] if items else None

    def sync_pause(self, job: dict, suspended: bool, wait: int) -> int:
        """Put ``suspended`` on the Job's Workload. Non-zero when it cannot.

        Kueue OWNS ``spec.suspend`` for a Workload it has admitted and flips
        it back within seconds, so the rendered field is intent, not
        enforcement; ``spec.active`` on the Workload is the lever that holds.
        Idempotent -- a Workload that already agrees is left alone, which is
        what makes a re-apply of an unchanged repo issue no patch at all.

        A Workload appears a moment AFTER its Job, and for a campaign that is
        paused in git that moment is exactly the window in which Kueue would
        admit and start it -- so a paused campaign waits, and fails loudly if
        the Workload never turns up. A campaign that is NOT paused needs no
        wait: a Workload that does not exist is not admitted either, and the
        next apply catches it.
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
