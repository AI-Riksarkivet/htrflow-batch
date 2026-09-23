"""kubernetes.client adapter for the read API.

Read-only but for one write: every method here is a get/list against Jobs,
ConfigMaps or Pods, except ``apply_configmap``, which server-side applies
the per-campaign status ConfigMap this service is the only observer of
(B76). Nothing here ever deletes, and nothing touches a Job or a Pod — the
RBAC granted to the service is get/list plus create/patch on
ConfigMaps (charts/htrflow-batch/templates/web.yaml), and a test greps this
package's source to keep it that way.

``Reader`` returns the plain dicts the Kubernetes API server itself sends
back (camelCase field names), which is exactly the shape ``projection.py``'s
pure functions expect.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Protocol

from kubernetes import client, config
from pydantic import BaseModel, ConfigDict, Field
from urllib3.exceptions import HTTPError

from .projection import KIND_LABEL, STATUS_KIND, WEB_MANAGER, pod_fields

#: Selects campaign progress Jobs only — excludes the per-pipeline warm-up
#: Jobs, which carry ``managed-by=converter`` too but not ``app`` or
#: ``campaign`` (packages/converter/src/htrflow_converter/render.py).
CAMPAIGN_LABELS = {
    "app": "htrflow-batch",
    "htrflow.riksarkivet.se/managed-by": "converter",
}
LABEL_SELECTOR = ",".join(f"{k}={v}" for k, v in CAMPAIGN_LABELS.items())


def is_campaign(job: dict) -> bool:
    """Whether a Job read by name is one ``LABEL_SELECTOR`` would list. The
    detail route reads any name it is given, and answered -- and wrote a
    status record for -- a warm-up Job or anything else in the namespace
    as though it were a campaign (2026-09-23 audit)."""
    labels = (job.get("metadata") or {}).get("labels") or {}
    return all(labels.get(k) == v for k, v in CAMPAIGN_LABELS.items())


_WARMUP_SELECTOR = "app=htrflow-warmup,htrflow.riksarkivet.se/managed-by=converter"

#: Both ConfigMaps a campaign has: the converter's record (``volumes.txt``,
#: provenance) and the status one this service writes beside it. Neither the
#: pipeline ConfigMaps nor anything else in the namespace carries a campaign
#: label, so the existence check is the whole filter.
CAMPAIGN_CONFIGMAPS = (
    "htrflow.riksarkivet.se/managed-by=converter,htrflow.riksarkivet.se/campaign"
)

#: Server-side apply, like `htrflow-campaigns apply`: one request that
#: creates the status ConfigMap or updates exactly the fields this manager
#: owns, with no read-modify-write race against a concurrent request.
_APPLY_PATCH = "application/apply-patch+yaml"

#: A list that answers with each object's ``metadata`` and nothing else --
#: what `kubectl get --output-watch-events=false` uses under the hood. The
#: campaign record's ``data`` is the campaign's whole volume list, and the
#: list route never reads it (2026-09-14 audit).
# A LIST asks for the List form: the API server answers 406 to a list request
# whose Accept names the single-object form, and does not fall back to the
# plain JSON offered after the comma (seen live on a 1.35 server).
PARTIAL_METADATA = (
    "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"
)
FIELD_MANAGER = WEB_MANAGER

_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_DEFAULT_NAMESPACE = "htr-batch"


def _own_namespace() -> str:
    try:
        with open(_NAMESPACE_FILE) as f:
            return f.read().strip() or _DEFAULT_NAMESPACE
    except OSError:
        return _DEFAULT_NAMESPACE


class Config(BaseModel):
    """The web front's whole env contract — `app.py` and `__main__.py` read no
    environment of their own. `HTRFLOW_`-prefixed: an operator's settings for a
    service, where the wrapper's are bare, an in-pod contract the Job writes."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    public_results_base: str = Field("", alias="HTRFLOW_PUBLIC_RESULTS_BASE")
    #: Where THIS POD reaches the results bucket -- not necessarily the same
    #: address a browser can resolve. On the PoC `public_results_base` is a
    #: `localhost` URL reached through an SSH forward (docs:
    #: development/local-k3s "Two S3 endpoints"): the API pod's own
    #: ProgressReader would resolve that `localhost` to itself and never
    #: reach RustFS. Defaults to `public_results_base` (real AWS, or any
    #: deployment where the same URL really does work from inside the
    #: cluster); the chart sets it explicitly for the PoC
    #: (`web.internalResultsBase`).
    internal_results_base: str = Field("", alias="HTRFLOW_INTERNAL_RESULTS_BASE")
    namespaces: tuple[str, ...] = Field((), alias="HTRFLOW_NAMESPACES")
    static_dir: str = Field("", alias="HTRFLOW_WEB_STATIC")
    site_only: bool = Field(False, alias="HTRFLOW_WEB_SITE_ONLY")
    #: The tag the image was published under, baked in by the dockerfile
    #: (.docker/htrflow-web.dockerfile). "dev" outside an image.
    batch_version: str = Field("dev", alias="HTRFLOW_BATCH_VERSION")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        get = os.environ.get if env is None else env.get
        base = (get("HTRFLOW_PUBLIC_RESULTS_BASE") or "").rstrip("/")
        internal_base = (get("HTRFLOW_INTERNAL_RESULTS_BASE") or "").rstrip("/") or base
        site_only = bool(get("HTRFLOW_WEB_SITE_ONLY"))  # any non-empty value
        if not base and not site_only:  # site-only builds no result URL
            raise RuntimeError("HTRFLOW_PUBLIC_RESULTS_BASE is required")
        names = [n.strip() for n in (get("HTRFLOW_NAMESPACES") or "").split(",")]
        return cls(
            HTRFLOW_PUBLIC_RESULTS_BASE=base,
            HTRFLOW_INTERNAL_RESULTS_BASE=internal_base,
            HTRFLOW_NAMESPACES=tuple(filter(None, names)) or (_own_namespace(),),
            HTRFLOW_WEB_STATIC=get("HTRFLOW_WEB_STATIC") or "",
            HTRFLOW_WEB_SITE_ONLY=site_only,
            HTRFLOW_BATCH_VERSION=get("HTRFLOW_BATCH_VERSION") or "dev",
        )


class ClusterUnavailable(Exception):
    """The API server did not answer this read. Every way that can happen --
    a 403 after an RBAC change, a 429, a connection that timed out -- arrives
    here as one exception, so ``app.py`` has one thing to answer with (a 502)
    instead of letting a client error escape as a bare 500 (2026-09-14
    audit). 404 is not one of these: a missing object is ``None``."""


class ApplyConflict(Exception):
    """Another field manager owns a field of the record this service tried to
    write. Contention, not a denied grant: `htrflow-campaigns apply` writes
    the same record from the live Job once a campaign is over, and those
    terminal values are the authoritative ones. Its own exception so
    ``app.py`` does not treat it as a namespace whose RBAC went away
    (2026-09-14 review)."""


#: (connect, read) seconds for every call to the API server. Without one a
#: connection that hung held its worker thread for ever, and a detail
#: request makes several calls in a row (2026-09-23 audit). The read half is
#: per socket read, so it bounds a body that stops arriving too. A list of a
#: few thousand objects answers in well under it.
REQUEST_TIMEOUT = (3.0, 10.0)

#: Pods per list page. Small enough that one page of whole pod objects is a
#: few MB at most; the API server hands out the next with a continue token.
POD_PAGE = 250


def _read(api: object, method: str, *args: object, **kwargs: object) -> dict | None:
    """Call a get/list method with ``_preload_content=False`` and decode the
    raw server JSON, so callers get the same camelCase dicts the API server
    sends — no typed-model round trip. 404 -> ``None``."""
    fn = getattr(api, method)
    try:
        resp = fn(
            *args,
            _preload_content=False,
            _request_timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
        # Read inside the try: the body arrives after the headers, and a
        # read that times out there is the same silence as one before them.
        return json.loads(resp.data)
    except client.ApiException as e:
        if e.status == 404:
            return None
        raise ClusterUnavailable(f"{method}: {e.status}") from e
    except HTTPError as e:  # urllib3: refused, timed out, TLS
        raise ClusterUnavailable(f"{method}: {type(e).__name__}") from e


class ReaderLike(Protocol):
    """What ``app.py`` asks of whatever it is handed: ``Reader`` in a pod,
    ``NoCluster`` in site-only mode, a fake in the tests. Written down so the
    doubles cannot drift from the real adapter -- a fake that answers with
    one fewer argument passes its own tests and proves nothing about the
    route (2026-09-14 audit); a test holds every signature below against
    each of them, parameter names included.

    ``apply_configmap`` is part of it like any other call. Left out, with
    ``app.py`` asking for the attribute instead, a rename of the real one
    passed every test and silently stopped every status write (2026-09-23
    audit). Site-only mode answers it with the same 503 as everything else,
    and never gets that far: its first read already refused.
    """

    cfg: Config | None

    def list_jobs(self) -> list[dict]: ...
    def list_warmups(self) -> list[dict]: ...
    def get_job(self, namespace: str, name: str) -> dict | None: ...
    def get_configmap(self, namespace: str, name: str) -> dict | None: ...
    def list_configmaps(self) -> list[dict]: ...
    def list_pods(self, namespace: str, job_name: str) -> list[dict]: ...
    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = FIELD_MANAGER
    ) -> None: ...


class Reader:
    cfg: Config

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def _list_jobs(self, selector: str) -> list[dict]:
        jobs: list[dict] = []
        for ns in self.cfg.namespaces:
            body = _read(self.batch, "list_namespaced_job", ns, label_selector=selector)
            jobs.extend((body or {}).get("items", []))
        return jobs

    def list_jobs(self) -> list[dict]:
        return self._list_jobs(LABEL_SELECTOR)

    def list_warmups(self) -> list[dict]:
        return self._list_jobs(_WARMUP_SELECTOR)

    def get_job(self, namespace: str, name: str) -> dict | None:
        return _read(self.batch, "read_namespaced_job", name, namespace)

    def get_configmap(self, namespace: str, name: str) -> dict | None:
        return _read(self.core, "read_namespaced_config_map", name, namespace)

    def list_configmaps(self) -> list[dict]:
        """Both ConfigMaps of every campaign in every namespace, in one list.

        Two calls per namespace, not one, and the difference is `data`
        (2026-09-14 audit). The record's `data` is `volumes.txt` -- one line
        per volume, megabytes for a real backfill -- and the list route reads
        nothing off it but labels and dates, so it is asked for as
        PartialObjectMetadata and those bytes never reach this process. The
        status ConfigMap's `data` IS the reaped campaign's row, so that one
        is fetched whole; it is a handful of short fields.
        """
        cms: list[dict] = []
        for ns in self.cfg.namespaces:
            cms.extend(self._records(ns))
            body = _read(
                self.core,
                "list_namespaced_config_map",
                ns,
                label_selector=f"{CAMPAIGN_CONFIGMAPS},{KIND_LABEL}={STATUS_KIND}",
            )
            cms.extend((body or {}).get("items", []))
        return cms

    def _records(self, namespace: str) -> list[dict]:
        """The campaign records of one namespace, metadata only. The typed
        client overwrites `Accept` on every generated method, so this is the
        one call this adapter makes through ``call_api`` itself."""
        try:
            resp = self.core.api_client.call_api(
                "/api/v1/namespaces/{namespace}/configmaps",
                "GET",
                {"namespace": namespace},
                [
                    (
                        "labelSelector",
                        f"{CAMPAIGN_CONFIGMAPS},{KIND_LABEL}!={STATUS_KIND}",
                    )
                ],
                {"Accept": PARTIAL_METADATA},
                auth_settings=["BearerToken"],
                _preload_content=False,
                _request_timeout=REQUEST_TIMEOUT,
            )
            return json.loads(resp.data).get("items", [])
        except client.ApiException as e:
            raise ClusterUnavailable(f"list records: {e.status}") from e
        except HTTPError as e:
            raise ClusterUnavailable(f"list records: {type(e).__name__}") from e

    def apply_configmap(
        self, body: dict, force: bool = False, manager: str = FIELD_MANAGER
    ) -> None:
        """The one write this service makes. Raises like any other client
        call — ``app.py`` logs it and answers the request anyway, because a
        status page that 500s when it cannot write a record is worse than
        one whose record is a few minutes old.

        Deliberately NOT forced (2026-09-14 audit). `htrflow-campaigns
        apply` writes this same record from the live Job once a campaign is
        over, and those terminal values are the authoritative ones; forcing
        would take the fields back off it on every poll of an open status
        page. A 409 stands as an ``ApplyConflict`` -- contention, not a
        refusal -- and is never sent again as it is: it means a field another
        manager owns or a precondition this request's read no longer meets,
        and the same request meets the same answer (2026-09-23 review). The
        next poll reads the record afresh. ``force`` is only ever asked for
        a record of another Job (``projection.record_write``), and then with
        the ``resourceVersion`` it read in ``body``: the API server holds
        the apply to that version. ``manager`` is the field manager sent:
        ``failedVolumes`` has one of its own (``projection.FAILURES_MANAGER``).
        """
        meta = body["metadata"]
        extra = {"force": True} if force else {}
        try:
            self.core.patch_namespaced_config_map(
                meta["name"],
                meta["namespace"],
                body,
                field_manager=manager,
                _content_type=_APPLY_PATCH,
                _preload_content=False,
                _request_timeout=REQUEST_TIMEOUT,
                **extra,
            )
        except client.ApiException as e:
            if e.status == 409:
                raise ApplyConflict(meta["name"]) from e
            raise ClusterUnavailable(f"apply {meta['name']}: {e.status}") from e
        except HTTPError as e:
            raise ClusterUnavailable(f"apply {meta['name']}: {type(e).__name__}") from e

    def list_pods(self, namespace: str, job_name: str) -> list[dict]:
        """One Job's pods that are not Succeeded, a page at a time, each
        trimmed to ``projection.pod_fields`` before the next page is read.

        A Job keeps every pod of every index until it is deleted -- up to
        ``backoffLimitPerIndex + 1`` per index -- and read whole, a campaign
        of a few thousand volumes was tens of MB of JSON per poll against
        the pod's memory limit (2026-09-23 audit). A succeeded pod belongs
        to a done index, which the Job's ``completedIndexes`` already says,
        so the API server keeps those; what is left is running and failed
        pods, the ones a row's state and reason are read from, and a page
        of them is the most this process ever holds whole."""
        pods: list[dict] = []
        token: str | None = None
        while True:
            paging = {"_continue": token} if token else {}
            body = _read(
                self.core,
                "list_namespaced_pod",
                namespace,
                label_selector=f"batch.kubernetes.io/job-name={job_name}",
                field_selector="status.phase!=Succeeded",
                limit=POD_PAGE,
                **paging,
            )
            pods.extend(pod_fields(p) for p in (body or {}).get("items", []))
            token = ((body or {}).get("metadata") or {}).get("continue")
            if not token:
                return pods
