"""The API server, as ``htrflow-campaigns apply`` needs it: server-side
apply, prune, and the Kueue pause sync that makes ``suspend:`` in git hold.

Not ``kubectl`` as a subprocess: same library and auth path as
``packages/web``, no binary to put on a CI image's ``PATH``, exceptions
instead of exit codes -- and a prune we own, rather than ``kubectl apply
--prune``'s client-side sweep, whose semantics have been "deprecated,
replaced by an alpha flag" for several releases now.

``_preload_content=False`` on the typed APIs, so what comes back is the
plain JSON the API server sent -- the shape the rendered manifests are in.

Every cluster problem this module knows how to explain crosses its boundary
as a ``ClusterError``: one sentence, not a ``kubernetes``/``urllib3``
traceback. ``cli._apply`` prints it and exits 1.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from .models import STATUS_SUFFIX
from .render import CAMPAIGN_SELECTOR, WARMUP_PREFIX

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
#: Characters of a server message this package will repeat. The branch
#: below is the one that carries the API server's own words, and a 422 whose
#: `details.causes` are empty still has the whole rejected value in them --
#: a pod template is thousands of characters of Go struct. This is a
#: terminal line, so it is cut rather than allowed to flood one.
MAX_MESSAGE = 300
#: (connect, read) seconds on every request. The client sends none by
#: default, so a connection the API server (or a load balancer between) has
#: half-closed leaves the apply blocked in `recv` for ever -- no deadline of
#: its own, and a CI job that never returns. The read side is generous: a
#: `list` of a large namespace is slow, while a connect that takes five
#: seconds is a server that is not there.
REQUEST_TIMEOUT = (5, 60)

#: Statuses that say "not now" rather than "not this request": a rate limit,
#: and the 5xx an apiserver being upgraded, a load balancer with no healthy
#: backend or an overloaded etcd answers with. An apply is a long sequence of
#: requests against a control plane that is none of it under our control, and
#: one of these used to leave a campaign unapplied and the operator
#: re-running the whole command. Everything else -- 409, 403, 422 -- is an
#: answer about the request itself, and waiting changes nothing.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Retries after the first attempt, and the seconds before each: 1, 2, 4.
#: Bounded on purpose -- an apply that hangs on a dead control plane is worse
#: than one that says so.
RETRIES = 3
RETRY_BACKOFF = 1

#: Seconds a replaced Job is waited for. Background propagation returns at
#: once and the object lingers while its pods go, so this covers a pod's
#: grace period and no more -- past that the apply says so and stops.
DELETE_WAIT = 60


class ClusterError(Exception):
    """A cluster problem this module already has a one-sentence answer for."""


#: What to do about a Job the API server will not take, by what the Job is.
#: One sentence used to serve both, and it was the warm-up's: a campaign Job
#: carries no recipe of its own, so "a changed recipe is a new pipeline file"
#: sent its reader to edit a file that is not the one in front of them.
_WARMUP_ADVICE = (
    "a pipeline id is a permanent name for a recipe, so a changed recipe is "
    "a new pipeline file, and a Job that has to change is deleted and "
    "created again"
)
_CAMPAIGN_ADVICE = (
    "a live campaign's Job cannot change, so finish or remove the campaign, then apply"
)


class ImmutableField(ClusterError):
    """An apply the API server refused because it would change a field that
    cannot change once the object exists.

    A class of its own rather than one more sentence, because ``cli._apply``
    ACTS on it and the right action differs by object: a warm-up Job is
    idempotent and holds nothing (its marker sits on the cache PVC), so a
    changed one is deleted and created again; a campaign Job's completed
    indexes and results ARE the campaign, so that one is reported and left
    exactly where it is.
    """

    #: The field a Job refuses most: its pod template is fixed at create.
    POD_TEMPLATE = "spec.template"

    def __init__(self, kind: str, name: str, fields: tuple[str, ...]) -> None:
        self.kind, self.name, self.fields = kind, name, fields
        if self.POD_TEMPLATE in fields:
            what = "the pod template changed and a Job's pod template is immutable"
        else:
            what = f"{', '.join(fields)} changed and is immutable"
        advice = _WARMUP_ADVICE if name.startswith(WARMUP_PREFIX) else _CAMPAIGN_ADVICE
        super().__init__(f"{kind} {name}: {what} once the Job exists — {advice}")


def _immutable_fields(e: ApiException) -> tuple[str, ...]:
    """The fields an ``is invalid`` refusal says cannot change.

    Read out of ``details.causes``, never out of ``message``: the message
    quotes the whole rejected value back, and for a pod template that is a
    page of Go struct dump (``core.PodTemplateSpec{ObjectMeta:v1.ObjectMeta
    {…}}``) with the three words that matter at the end of it.
    """
    if e.status != 422 or not e.body:
        return ()
    with contextlib.suppress(json.JSONDecodeError, TypeError, KeyError):
        causes = json.loads(e.body)["details"]["causes"]
        return tuple(
            c["field"] for c in causes if "immutable" in (c.get("message") or "")
        )
    return ()


def _api_error(
    verb: str, kind: str, name: str, namespace: str, e: ApiException
) -> ClusterError:
    target = f"{kind}/{name}" if name else kind
    if e.status in (401, 403):
        return ClusterError(
            f"not allowed to {verb} {target} in {namespace}: {e.reason} — the "
            "htrflow-batch chart renders the needed ServiceAccount behind "
            "apply.rbac.enabled"
        )
    if e.status == 404 and kind == "Workload" and verb == "list":
        return ClusterError(
            "Kueue is not installed in this cluster (no workloads.kueue.x-k8s.io) "
            "— htrflow-campaigns apply needs it to honour suspend:"
        )
    fields = _immutable_fields(e)
    if fields:
        return ImmutableField(kind, name, fields)
    message = ""
    if e.body:
        with contextlib.suppress(json.JSONDecodeError, TypeError, KeyError):
            # Reflowed: an admission webhook's rejection (Kyverno's, say)
            # arrives as a paragraph with blank lines in it, and everything
            # else this package prints is one sentence per problem.
            message = " " + " ".join(json.loads(e.body)["message"].split())
        if len(message) > MAX_MESSAGE:
            message = message[:MAX_MESSAGE].rstrip() + " …"
    return ClusterError(f"{verb} {target}: {e.status} {e.reason}{message}")


def _unreachable(e: HTTPError) -> ClusterError:
    """``MaxRetryError`` -- a bad or unreachable ``KUBECONFIG`` server -- is
    a ``urllib3.exceptions.HTTPError``, not an ``ApiException``: catching
    only the latter misses the commonest failure of all. The host is the one
    the loaded config points at, not something parsed out of ``e``."""
    host = client.Configuration.get_default_copy().host
    reason = str(getattr(e, "reason", None) or e).splitlines()[0]
    return ClusterError(f"cannot reach the Kubernetes API server at {host}: {reason}")


@contextlib.contextmanager
def _errors(verb: str, kind: str, name: str, namespace: str):
    """Every API call goes through here: a problem leaves as a sentence."""
    try:
        yield
    except ApiException as e:
        raise _api_error(verb, kind, name, namespace, e) from e
    except HTTPError as e:
        raise _unreachable(e) from e


def _retrying(fn: Any, *args: Any, gone_is_done: bool = False, **kwargs: Any) -> Any:
    """``fn(*args, **kwargs)``, retried while the server says "not now".

    ``gone_is_done`` is for a DELETE: a retry that finds the object gone
    found the work of the attempt before it -- that one reached the API
    server and only its answer was lost -- so the 404 is this call's own
    success, not a missing object. A 404 on the FIRST attempt still stands:
    nothing of ours deleted that, so the caller was wrong about it.
    """
    for attempt in range(RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except ApiException as e:
            if attempt and gone_is_done and e.status == 404:
                return None
            if e.status not in RETRY_STATUSES or attempt == RETRIES:
                raise
            time.sleep(RETRY_BACKOFF << attempt)


def _raw(
    verb: str, kind: str, name: str, namespace: str, fn: Any, *args: Any, **kwargs: Any
) -> dict:
    with _errors(verb, kind, name, namespace):
        return json.loads(
            _retrying(
                fn,
                *args,
                _preload_content=False,
                _request_timeout=REQUEST_TIMEOUT,
                **kwargs,
            ).data
        )


class Cluster:
    """One namespace, reached through the kubeconfig or the pod's own token."""

    def __init__(self, namespace: str) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            try:
                config.load_kube_config()
            except config.ConfigException as e:
                raise ClusterError(
                    "no Kubernetes credentials: not running in a pod and no "
                    "usable kubeconfig (set KUBECONFIG, or run kubectl config "
                    "use-context)"
                ) from e
        self.namespace = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

    def _method(self, kind: str, verb: str, name: str = "") -> Any:
        if kind not in _KINDS:
            raise ClusterError(
                f"{kind}/{name}: htrflow-campaigns apply only handles "
                f"{', '.join(_KINDS)}"
            )
        api, noun = _KINDS[kind]
        return getattr(getattr(self, api), f"{verb}_namespaced_{noun}")

    def apply(self, obj: dict) -> dict:
        """Server-side apply ``obj``; returns what the server stored.

        ``force=True`` takes the fields back from whatever manager owns them
        -- a Job applied by `kubectl` before this change, a hand edit --
        which is the "git is the truth" rule the campaigns repo runs on.
        """
        kind, name = obj["kind"], obj["metadata"]["name"]
        return _raw(
            "apply",
            kind,
            name,
            self.namespace,
            self._method(kind, "patch", name),
            name,
            self.namespace,
            obj,
            field_manager=FIELD_MANAGER,
            force=True,
            _content_type=APPLY_PATCH,
        )

    def replace_job(self, obj: dict) -> dict:
        """Delete Job ``obj`` and apply it again -- the only way to give a
        Job a pod template it did not start with.

        Only ever called for a warm-up Job (``cli._apply_object``): the
        delete takes the Job's pods with it, and a campaign Job's pods are
        the campaign. The wait is not politeness: an apply against a name
        the API server still holds patches the OLD Job and is refused all
        over again, so the create has to follow the deletion, not the
        delete call.
        """
        name = obj["metadata"]["name"]
        with _errors("delete", "Job", name, self.namespace):
            _retrying(
                self._method("Job", "delete"),
                name,
                self.namespace,
                gone_is_done=True,
                propagation_policy="Background",
                _request_timeout=REQUEST_TIMEOUT,
            )
        for _ in range(DELETE_WAIT):
            if self.get("Job", name) is None:
                return self.apply(obj)
            time.sleep(1)
        raise ClusterError(
            f"Job {name} was deleted so it could be created with its new pod "
            f"template, but it was still there {DELETE_WAIT}s later — re-run "
            "the apply"
        )

    def get(self, kind: str, name: str) -> dict | None:
        """The live object, or ``None`` when there is none. What lets an
        apply tell a campaign nobody has run from one whose Job the TTL
        reaped: the second still has its ConfigMaps (B76)."""
        try:
            body = _retrying(
                self._method(kind, "read", name),
                name,
                self.namespace,
                _preload_content=False,
                _request_timeout=REQUEST_TIMEOUT,
            )
        except ApiException as e:
            if e.status == 404:
                return None
            raise _api_error("get", kind, name, self.namespace, e) from e
        except HTTPError as e:
            raise _unreachable(e) from e
        return json.loads(body.data)

    def prune(self, rendered: set[tuple[str, str]]) -> None:
        """Delete every labelled Job/ConfigMap not in ``rendered``.

        ``rendered`` is ``(kind, name)`` for **all** rendered objects,
        pipelines included: pruning against the campaigns alone would delete
        the pipeline ConfigMaps and warm-up Jobs the same apply just wrote.
        """
        for kind in _KINDS:
            listed = _raw(
                "list",
                kind,
                "",
                self.namespace,
                self._method(kind, "list"),
                self.namespace,
                label_selector=CAMPAIGN_SELECTOR,
            )
            for item in listed.get("items", []):
                name = item["metadata"]["name"]
                if (kind, name) in rendered or self._kept_status(kind, name, rendered):
                    continue
                extra = {"propagation_policy": "Background"} if kind == "Job" else {}
                with _errors("delete", kind, name, self.namespace):
                    _retrying(
                        self._method(kind, "delete"),
                        name,
                        self.namespace,
                        gone_is_done=True,
                        _request_timeout=REQUEST_TIMEOUT,
                        **extra,
                    )
                print(f"pruned: {kind}/{name}")

    @staticmethod
    def _kept_status(kind: str, name: str, rendered: set[tuple[str, str]]) -> bool:
        """A campaign's status ConfigMap (B76) is written by the read API
        and never rendered, so a prune would delete it on sight. It belongs
        to the campaign ConfigMap it is named after: kept while that one is
        rendered, pruned with it when the campaign file leaves git."""
        if kind != "ConfigMap" or not name.endswith(STATUS_SUFFIX):
            return False
        return (kind, name.removesuffix(STATUS_SUFFIX)) in rendered

    def _workload(self, uid: str) -> dict | None:
        """The Kueue Workload of the Job with ``uid``. Kueue labels it with
        that uid, the only link that survives a delete/recreate of the Job."""
        with _errors("list", "Workload", "", self.namespace):
            listed = _retrying(
                self.custom.list_namespaced_custom_object,
                *_KUEUE,
                self.namespace,
                _WORKLOADS,
                label_selector=f"{_JOB_UID_LABEL}={uid}",
                _request_timeout=REQUEST_TIMEOUT,
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
                f"{wait}s — the pause is NOT enforced; re-run the apply",
                file=sys.stderr,
            )
            return 1
        want = not suspended
        if wl.get("spec", {}).get("active", True) == want:
            return 0
        wl_name = wl["metadata"]["name"]
        print(f"{name}: workload/{wl_name} active={str(want).lower()}")
        with _errors("patch", "Workload", wl_name, self.namespace):
            _retrying(
                self.custom.patch_namespaced_custom_object,
                *_KUEUE,
                self.namespace,
                _WORKLOADS,
                wl_name,
                {"spec": {"active": want}},
                _content_type=MERGE_PATCH,
                _request_timeout=REQUEST_TIMEOUT,
            )
        return 0
