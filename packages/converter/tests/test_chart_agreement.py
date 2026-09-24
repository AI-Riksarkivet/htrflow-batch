"""The converter and charts/htrflow-batch agree by naming convention only.

Neither side can see the other: the chart creates the queue, the S3 Secret
and the model-cache PVC, and the converter renders Jobs that *reference*
them by name. Until this test they agreed because two files said the same
word, and `examples/campaigns/converter.yaml` asked a human to keep it that
way in a comment. A rename on one side now fails here.

Only keys both sides have are checked: `namespace` is the release namespace
(a `helm -n` argument, not a value) and `runtime_class` has no chart key at
all. `hf_token_secret` has one since the job-shape policy: the Hub-token
Secret is still the operator's own object, but `hfToken.existingSecret` is
the one Secret a warm-up pod may read, so the two must name the same one.
docs/reference/configuration.md lists what is one-sided.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from htrflow_converter.models import ConverterConfig, Size
from htrflow_converter.render import CAMPAIGN_SELECTOR

ROOT = Path(__file__).parents[3]
CHART = ROOT / "charts" / "htrflow-batch"
EXAMPLE = ROOT / "examples" / "campaigns" / "converter.yaml"
CONVERTER_SRC = ROOT / "packages" / "converter" / "src" / "htrflow_converter"
JOB_SKELETON = CONVERTER_SRC / "manifests" / "campaign-job.yaml"

# The (converter field, chart values path) table is written once, in the
# generator that also prints it into docs/reference/configuration.md.
sys.path.insert(0, str(ROOT / "scripts"))
from config_reference import (  # noqa: E402
    AGREEMENTS,
    FLAVORS_PAIR,
    FREE_ENV,
    FRONTEND_DOC,
    IMAGE_ENV_DOC,
    LIST_AGREEMENTS,
    LOCAL_ONLY,
    PAGE,
    PAIRS,
    SECURITY,
    SURFACES,
    WEB_DEFAULT_DOC,
    _chart_rows,
    _model_rows,
    chart_web_env,
    frontend_rows,
    image_env,
    job_shape,
    overrides,
    render,
    script_exports,
    web_set_by,
    wrapper_set_by,
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _at(values: dict, path: str) -> object:
    for key in path.split("."):
        values = values[key]
    return values


def _default(field: str) -> object:
    """converter.yaml's default for ``field``: what a file that leaves it
    out gets."""
    return ConverterConfig.model_fields[field].get_default(call_default_factory=True)


def _disagreements(config: dict) -> list[str]:
    values = _load(CHART / "values.yaml")
    # A key a converter.yaml leaves out is the converter's default.
    config = {f: config.get(f, _default(f)) for f, _ in AGREEMENTS}
    return [
        f"`{field}` is {config[field]!r} but the chart's `{path}` is "
        f"{_at(values, path)!r} — they name one cluster object"
        for field, path in AGREEMENTS
        if config[field] != _at(values, path)
    ]


def test_converter_defaults_agree_with_the_chart_defaults():
    defaults = {f: _default(f) for f, _ in AGREEMENTS}
    assert _disagreements(defaults) == []


def test_the_example_campaigns_repo_agrees_with_the_chart_defaults():
    assert _disagreements(_load(EXAMPLE)) == []


#: The chart's own placeholder file (what it refuses to render without) and
#: the intents a render has to state; the apply identity on, for its Role.
HELM_SETS = (
    "network.web.allowPublicIngress=true",
    "security.policies.allowDisabled=true",
    "apply.rbac.enabled=true",
)


def _helm_objects(*sets: str) -> list[dict]:
    import shutil
    import subprocess

    if shutil.which("helm") is None:
        pytest.skip("helm not on PATH")
    cmd = ["helm", "template", "htr", str(CHART), "-n", "htr-batch"]
    cmd += ["-f", str(CHART / "ci" / "default-values.yaml")]
    for setting in (*HELM_SETS, *sets):
        cmd += ["--set", setting]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _rendered(kind: str, name: str, *sets: str) -> dict:
    return next(
        o
        for o in _helm_objects(*sets)
        if o["kind"] == kind and o["metadata"]["name"] == name
    )


def test_the_results_base_reaches_both_of_its_consumers():
    """One value under four names: `publicResultsBase` (chart) reaches the web
    front as `HTRFLOW_PUBLIC_RESULTS_BASE` and the wrapper as
    `PUBLIC_RESULTS_BASE`, which the converter fills from `converter.yaml`'s
    `public_results_base`. Renaming any one of the four strands a consumer,
    so both renders are read, not their source (test audit TA-infra-3)."""
    from htrflow_converter import render as render_objects
    from htrflow_converter.parse import load

    base = yaml.safe_load((CHART / "ci" / "default-values.yaml").read_text())[
        "publicResultsBase"
    ]
    fixture = ROOT / "packages" / "converter" / "tests" / "fixtures" / "good"
    campaigns, pipelines, cfg = load(
        fixture / "campaigns", fixture / "pipelines", fixture / "converter.yaml"
    )
    cfg = cfg.model_copy(update={"public_results_base": base})
    job = next(
        o
        for o in render_objects.campaign_objects(
            campaigns[0], pipelines["demo-v1"], cfg
        )
        if o["kind"] == "Job"
    )
    env = {
        e["name"]: e.get("value")
        for e in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["PUBLIC_RESULTS_BASE"] == base
    web = _rendered("Deployment", "htrflow-web")
    web_env = {
        e["name"]: e.get("value")
        for e in web["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert web_env["HTRFLOW_PUBLIC_RESULTS_BASE"] == base


def test_security_names_only_keys_the_generator_emits():
    """SECURITY is keyed by hand, next to the models it annotates -- a rename
    on either side must fail here, not just silently drop out of the page's
    Security column."""
    values = _load(CHART / "values.yaml")
    emitted = {
        k
        for _, _, _, m, doc in SURFACES
        for k, _ in (_model_rows(m, doc) if m else _chart_rows(values))
    }
    assert set(SECURITY) <= emitted
    assert set(WEB_DEFAULT_DOC) <= emitted


def test_the_configuration_page_is_what_the_generator_prints():
    """docs/reference/configuration.md is generated from the three models and
    the chart's values (`make config-reference`). Editing it by hand, or
    changing a default without regenerating, fails here — which is the whole
    reason the page is generated rather than written."""
    assert PAGE.read_text(encoding="utf-8") == render(), (
        f"{PAGE.relative_to(ROOT)} is not what scripts/config_reference.py "
        "prints — run `make config-reference`"
    )


def _grants(role: dict) -> set[tuple[str, str, str, tuple[str, ...]]]:
    """Every (group, resource, verb, resourceNames) a rendered Role grants --
    a rule naming several resources or verbs counted in full."""
    return {
        (g, r, v, tuple(rule.get("resourceNames", ())))
        for rule in role["rules"]
        for g in rule["apiGroups"]
        for r in rule["resources"]
        for v in rule["verbs"]
    }


def test_the_read_api_role_is_exactly_what_it_calls():
    """The read API computes every answer from a get or a list on Jobs, Pods
    and ConfigMaps, and writes one object: the per-campaign status ConfigMap
    (B76) -- `create` beside `patch`, since a server-side apply of an object
    that is not there yet is a create. Nothing watches, deletes or reads a
    Secret. Read off the rendered Role, so a rule listing several resources
    is seen whole (test audit TA-infra-2)."""
    role = _rendered("Role", "htrflow-web")
    read = {("batch", "jobs"), ("", "pods"), ("", "configmaps")}
    expected = {(g, r, v, ()) for g, r in read for v in ("get", "list")}
    expected |= {("", "configmaps", v, ()) for v in ("create", "patch")}
    assert _grants(role) == expected


def _names(config: dict) -> dict[str, list[str]]:
    values = _load(CHART / "values.yaml")
    return {
        f"{field} vs {path}": [config[field], [e[key] for e in _at(values, path)]]
        for field, path, key in LIST_AGREEMENTS
    }


def test_the_priority_classes_the_converter_accepts_are_the_ones_the_chart_ships():
    """A campaign's `priority:` is checked against `converter.yaml`'s list
    because Kueue never refuses an unknown class -- the Job just stays
    suspended, with no event, and reads "Queued" for ever. So the list has
    to be the chart's, and in the same order."""
    defaults = {f: _default(f) for f, _, _ in LIST_AGREEMENTS}
    for pair, (ours, theirs) in _names(defaults).items():
        assert ours == theirs, pair
    for pair, (ours, theirs) in _names(_load(EXAMPLE)).items():
        assert ours == theirs, pair


def _flavors(entries: list) -> dict[str, dict]:
    return {
        e["name"]: e["nodeLabels"] if isinstance(e, dict) else e.node_labels
        for e in entries
    }


#: converter.yaml's `flavors` for a chart installed with ci/full-values.yaml.
FULL_VALUES_FLAVORS = [
    {"name": "small-gpu", "nodeLabels": {"nvidia.com/gpu.product": "NVIDIA-L4"}},
    {
        "name": "large-gpu",
        "nodeLabels": {"nvidia.com/gpu.product": "NVIDIA-A100-SXM4-80GB"},
    },
]


def _cluster_flavors(values: str) -> dict[str, dict | None]:
    """What `apply` reads from a cluster the chart was installed into with
    `values`: the ClusterQueue's flavors and each one's node labels."""
    import shutil
    import subprocess

    if shutil.which("helm") is None:
        pytest.skip("helm not on PATH")
    cmd = ["helm", "template", "htr", str(CHART), "-n", "htr-batch"]
    cmd += ["-f", str(CHART / "ci" / "default-values.yaml"), "-f", str(CHART / values)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    objects = [d for d in yaml.safe_load_all(result.stdout) if d]
    labels = {
        o["metadata"]["name"]: (o.get("spec") or {}).get("nodeLabels", {})
        for o in objects
        if o["kind"] == "ResourceFlavor"
    }
    (queue,) = [o for o in objects if o["kind"] == "ClusterQueue"]
    return {
        f["name"]: labels.get(f["name"])
        for group in queue["spec"]["resourceGroups"]
        for f in group["flavors"]
    }


def test_the_flavors_a_size_names_are_the_ones_the_chart_describes():
    """B105: a size's flavor is rendered as the flavor's node labels on the
    pod, so converter.yaml's names and labels must be the chart's. `apply`
    holds them to the ClusterQueue it reads (`cli.flavor_mismatch`); here
    the same comparison runs against what the chart renders. Order is not
    compared: the converter never relies on it."""
    from htrflow_converter.cli import flavor_mismatch

    chart = _flavors(_load(CHART / "values.yaml")["queue"]["flavors"])
    assert _flavors(_default("flavors")) == chart
    assert _flavors(_load(EXAMPLE).get("flavors", [])) == chart
    assert FLAVORS_PAIR in PAIRS.items()
    live = _cluster_flavors("ci/full-values.yaml")
    ours = _flavors(FULL_VALUES_FLAVORS)
    assert flavor_mismatch(ours, live) is None
    typo = {**ours, "large-gpu": {"nvidia.com/gpu.product": "NVIDIA-A100"}}
    assert flavor_mismatch(typo, live) == (
        "flavor large-gpu is nvidia.com/gpu.product=NVIDIA-A100-SXM4-80GB in the"
        " cluster and nvidia.com/gpu.product=NVIDIA-A100 in converter.yaml"
    )
    missing = {"small-gpu": ours["small-gpu"]}
    assert flavor_mismatch(missing, live) == (
        "the cluster has flavor large-gpu, which converter.yaml does not list"
    )


WORKFLOWS = [
    ROOT / "examples" / "campaigns" / ".github" / "workflows" / "render.yml",
    CONVERTER_SRC / "ci" / "github" / ".github" / "workflows" / "render.yml",
    CONVERTER_SRC / "ci" / "azure" / "azure-pipelines.yml",
]


def _kyverno_step(workflow: Path) -> str:
    doc = _load(workflow)
    if "stages" in doc:  # Azure Pipelines: stage -> job -> `bash:` steps
        policy = next(s for s in doc["stages"] if s["stage"] == "Policy")
        steps = [step for job in policy["jobs"] for step in job["steps"]]
        return next(s["bash"] for s in steps if s.get("displayName") == "Kyverno")
    steps = doc["jobs"]["policy"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Kyverno")


def _service_account(template: str) -> str:
    text = (CHART / "templates" / template).read_text(encoding="utf-8")
    return re.search(r"kind: ServiceAccount\nmetadata:\n  name: ([\w-]+)", text).group(
        1
    )


def test_the_campaigns_ci_checks_policies_as_the_apply_identity():
    """3067: `rbac-scope` matches ConfigMap writes by the web ServiceAccount.
    `kyverno apply` with no `--userinfo` has no requester to rule that out,
    so the web rule fired on every pipeline and campaign ConfigMap and the
    policy job failed on every valid campaign. What the cluster admits from
    a campaigns repo is written by `htrflow-campaigns apply`, so CI has to
    submit as that identity -- the one apply-rbac.yaml creates, never the
    one the web rule is scoped to."""
    apply_sa = _service_account("apply-rbac.yaml")
    web_sa = _service_account("web.yaml")
    assert apply_sa != web_sa
    for workflow in WORKFLOWS:
        run = _kyverno_step(workflow).replace("\\\n", " ")
        assert re.search(r"kyverno apply .*--userinfo ", run), workflow
        usernames = re.findall(r"username: (\S+)", run)
        assert usernames == [
            f"system:serviceaccount:${{POLICY_NAMESPACE}}:{apply_sa}"
        ], workflow


def test_the_policy_tests_run_the_kyverno_release_a_campaigns_repo_runs():
    """test_policy_admission.py stands in for a campaigns repo's policy job,
    and says so; that is only true while the CLI the dagger test container
    lifts is the release every campaigns CI template installs. Kyverno's
    JMESPath and its CLI's admission stand-in both move between releases,
    so a bump on one side alone would prove the policies against a binary
    no pull request runs (audit 0923 T-8)."""
    main_go = (ROOT / ".dagger" / "main.go").read_text(encoding="utf-8")
    ours = re.search(r'kyvernoCliImage\s*=\s*"[^":]+:(v[\d.]+)@sha256:', main_go)
    assert ours, "kyvernoCliImage is not a tag-and-digest reference"
    for workflow in WORKFLOWS:
        doc = _load(workflow)
        pinned = doc["variables" if "stages" in doc else "env"]["KYVERNO_VERSION"]
        assert pinned == ours.group(1), workflow


def test_the_job_shape_policy_holds_the_scripts_the_converter_renders():
    """The job-shape policy compares a Job's script with the converter's,
    character for character, from a copy in the chart (Helm cannot read the
    converter's manifests). A script changed on one side alone would refuse
    every campaign or warm-up Job at admission; this fails first, without a
    Kyverno CLI (audit 0923 D-2)."""
    policy = (CHART / "templates" / "policies" / "job-shape.yaml").read_text(
        encoding="utf-8"
    )
    copies = dict(re.findall(r"\{\{- \$(\w+) := `([^`]*)` \}\}", policy))
    for var, skeleton in (
        ("batchArgs", "campaign-job.yaml"),
        ("warmupArgs", "warmup-job.yaml"),
    ):
        job = _load(CONVERTER_SRC / "manifests" / skeleton)
        assert (
            copies[var] == job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        ), var


#: The API group each typed client class reaches. A client cluster.py
#: builds that is not listed fails the recording test by name.
_CLIENT_GROUPS = {
    "CoreV1Api": "",
    "BatchV1Api": "batch",
    "CoordinationV1Api": "coordination.k8s.io",
}
#: A client method's verb -> the RBAC verb the API server checks.
_RBAC_VERB = {
    "read": "get",
    "get": "get",
    "list": "list",
    "create": "create",
    "patch": "patch",
    "replace": "update",
    "delete": "delete",
}
_CLIENT_METHOD = re.compile(
    r"(read|get|list|create|patch|replace|delete)_(namespaced|cluster)_(\w+)"
)
#: What the recording client answers a Kueue read of the queue with: the
#: LocalQueue points at the chart's ClusterQueue, which has its one flavor.
_QUEUE_OBJECTS = {
    "localqueues": {"spec": {"clusterQueue": "htr-batch-cq"}},
    "clusterqueues": {
        "spec": {"resourceGroups": [{"flavors": [{"name": "default-flavor"}]}]}
    },
    "resourceflavors": {"spec": {}},
}


class _Answer:
    """What `_preload_content=False` hands back: a body and headers."""

    def __init__(self, body: object) -> None:
        self.data = json.dumps(body).encode()
        self.headers: dict = {}


class _Recording:
    """A Kubernetes client that records every request as the API server
    authorizes it -- (group, resource, verb, name) -- and answers from a
    small in-memory namespace."""

    def __init__(self, cls: str, log: list, absent: set) -> None:
        self.cls, self.log, self.absent = cls, log, absent

    def __getattr__(self, method: str):
        from kubernetes.client.exceptions import ApiException

        m = _CLIENT_METHOD.fullmatch(method)
        assert m, f"{self.cls}.{method} is not a namespaced call this test knows"
        verb, scope, noun = m.groups()

        def call(*args, **kwargs):
            if noun == "custom_object" and scope == "cluster":
                group, _version, resource, *rest = args
            elif noun == "custom_object":
                group, _version, _ns, resource, *rest = args
            else:
                assert self.cls in _CLIENT_GROUPS, f"add {self.cls}'s API group"
                group, resource = _CLIENT_GROUPS[self.cls], noun.replace("_", "") + "s"
                rest = [] if verb in ("list", "create") else [args[0]]
            name = rest[0] if rest else ""
            if verb == "create":
                name = args[1]["metadata"]["name"]
            self.log.append((group, resource, _RBAC_VERB[verb], name))
            if kwargs.get("_content_type") == "application/apply-patch+yaml":
                # A server-side apply of an object that is not there yet is
                # authorized as a create as well.
                self.log.append((group, resource, "create", name))
            if verb == "read" and name in self.absent:
                raise ApiException(status=404)
            if noun == "custom_object" and resource in _QUEUE_OBJECTS:
                return _QUEUE_OBJECTS[resource]
            if noun == "custom_object":
                wl = {"metadata": {"name": "job-kyrk-1"}, "spec": {"active": True}}
                return {"items": [wl]} if verb == "list" else wl
            if noun == "lease":
                body = args[-1] if verb in ("create", "replace") else {}
                return _Answer(
                    {
                        "metadata": {"name": name, "resourceVersion": "1"},
                        "spec": (body or {}).get("spec", {}),
                    }
                )
            if verb == "list":
                return _Answer({"items": [{"metadata": {"name": "stale"}}]})
            # The converter's own object: one without its label is refused
            # before any write (``Cluster.claim``).
            key, value = CAMPAIGN_SELECTOR.split("=")
            return _Answer({"metadata": {"name": name, "labels": {key: value}}})

        return call


def _requests_apply_makes(monkeypatch) -> list[tuple[str, str, str, str]]:
    """Drive the real Cluster through every path `htrflow-campaigns apply`
    takes -- take the Lease, list, dry-run and apply, read, replace a
    warm-up, hold a suspend, pause, prune, renew the Lease on the way and
    release it -- against the recording client."""
    from htrflow_converter import cluster

    log: list = []
    absent = {cluster.LEASE, "htr-warmup-demo-v1"}
    clock = [1000.0]
    monkeypatch.setattr(cluster.time, "monotonic", lambda: clock[0])
    c = object.__new__(cluster.Cluster)
    c.namespace = "htr-batch"
    for attr, cls in (
        ("batch", "BatchV1Api"),
        ("core", "CoreV1Api"),
        ("coordination", "CoordinationV1Api"),
        ("custom", "CustomObjectsApi"),
    ):
        setattr(c, attr, _Recording(cls, log, absent))
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "kyrk"}}
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "campaign-kyrk"},
    }
    warm = {**job, "metadata": {"name": "htr-warmup-demo-v1"}}
    suspended = {
        "metadata": {
            "name": "kyrk",
            "managedFields": [
                {
                    "manager": cluster.FIELD_MANAGER,
                    "fieldsV1": {"f:spec": {"f:suspend": {}}},
                }
            ],
        },
        "spec": {"suspend": True},
    }
    with c.lease():
        for kind in ("Job", "ConfigMap"):
            c.labelled(kind)
        for obj in (job, cm):
            c.apply(obj, dry_run=True)
            c.apply(obj)
            c.get(obj["kind"], obj["metadata"]["name"])
        c.replace_job(warm)
        c.hold_suspend(suspended)
        clock[0] += cluster.LEASE_RENEW + 1  # the next request renews
        c.sync_pause({"metadata": {"name": "kyrk", "uid": "u-1"}}, True, 0)
        c.prune({("Job", "kyrk"), ("ConfigMap", "campaign-kyrk")})
        c.queue_flavors("htr-batch")
    return log


def _apply_role_grants() -> list[dict]:
    """The apply identity's rules as `helm template` renders them: its Role,
    and the ClusterRole that may read the queue's cluster-scoped objects by
    name (B105), bound to it."""
    objects = _helm_objects()
    role = next(
        o
        for o in objects
        if o["kind"] == "Role" and o["metadata"]["name"] == "htrflow-campaigns"
    )
    bound = [
        o["roleRef"]["name"]
        for o in objects
        if o["kind"] == "ClusterRoleBinding"
        and {
            "kind": "ServiceAccount",
            "name": "htrflow-campaigns",
            "namespace": "htr-batch",
        }
        in o["subjects"]
    ]
    cluster_rules = [
        r
        for o in objects
        if o["kind"] == "ClusterRole" and o["metadata"]["name"] in bound
        for r in o["rules"]
    ]
    return role["rules"] + cluster_rules


def test_the_apply_role_grants_exactly_the_requests_apply_makes(monkeypatch):
    """`htrflow-campaigns apply` run in the cluster has only this Role: a
    request it makes that the Role does not grant is a 403 halfway through
    an apply -- a Lease it cannot take fails every run closed -- and a grant
    no request needs is standing access for whoever holds the token. The
    requests are recorded from the real Cluster methods, not guessed from
    their text, and a rule that names `resourceNames` grants those names
    only (audit 0923 I-2)."""
    from htrflow_converter import cluster

    log = _requests_apply_makes(monkeypatch)
    rules = _apply_role_grants()
    for group, resource, verb, name in log:
        assert any(
            group in r["apiGroups"]
            and resource in r["resources"]
            and verb in r["verbs"]
            and (name in r.get("resourceNames", [name]))
            for r in rules
        ), (group, resource, verb, name)
    used = {(g, r, v) for g, r, v, _ in log}
    granted = {
        (g, r, v)
        for rule in rules
        for g in rule["apiGroups"]
        for r in rule["resources"]
        for v in rule["verbs"]
    }
    assert used == granted
    named = [r["resourceNames"] for r in rules if "resourceNames" in r]
    # The Lease, and the queue objects by the names the chart gives them:
    # nothing cluster-scoped beyond this release's own queue.
    assert named == [
        [cluster.LEASE],
        ["htr-batch"],
        ["htr-batch-cq"],
        ["default-flavor"],
    ]


def _job_shape_spec() -> dict:
    policy = (CHART / "templates" / "policies" / "job-shape.yaml").read_text(
        encoding="utf-8"
    )
    return yaml.safe_load(re.search(r"\{\{- \$spec := `([^`]*)`", policy).group(1))


@pytest.mark.parametrize(
    "role,skeleton,added",
    [
        # A named size adds the lookahead its /work bounds (B105).
        ("batch", "campaign-job.yaml", {"LOOKAHEAD_BYTES"}),
        ("warmup", "warmup-job.yaml", {"HF_TOKEN"}),
    ],
)
def test_the_job_shape_policy_holds_the_skeletons_env_mounts_and_security(
    role: str, skeleton: str, added: set
):
    """job-shape allows a campaign or warm-up Job exactly the env vars,
    mounts, Secret file mode and securityContexts of the converter's
    skeletons, from a copy in the chart. A skeleton that gains an env var or
    a mount without the chart would have every Job refused at admission;
    one that changes a pinned value, the same (audit 0923 I-1). `added` is
    what render.py adds on top of the skeleton (the Hub token, when
    converter.yaml names its Secret)."""
    spec = _job_shape_spec()
    shape = spec[role]
    pod = _load(CONVERTER_SRC / "manifests" / skeleton)["spec"]["template"]["spec"]
    main = pod["containers"][0]
    env = {e["name"]: e for e in main["env"]}
    named = set(shape["pinned"]) | set(shape["free"]) | set(shape["secretEnv"])
    named |= set(shape["fieldEnv"])
    assert named == set(env) | added
    for name, value in shape["pinned"].items():
        assert env[name] == {"name": name, "value": value}, name
    for name in set(shape["free"]) - added:
        assert "valueFrom" not in env[name], name
    assert set(shape["bytes"]) <= set(shape["free"])
    for name in set(shape["secretEnv"]) - added:
        assert list(env[name]["valueFrom"]) == ["secretKeyRef"], name
    for name, path in shape["fieldEnv"].items():
        assert env[name]["valueFrom"] == {"fieldRef": {"fieldPath": path}}, name
    assert [[m["name"], m["mountPath"]] for m in main["volumeMounts"]] == shape[
        "mounts"
    ]
    inits = [
        [m["name"], m["mountPath"]]
        for c in pod.get("initContainers", [])
        for m in c["volumeMounts"]
    ]
    assert inits == shape["initMounts"]
    assert all("env" not in c for c in pod.get("initContainers", []))
    assert pod["securityContext"] == spec["podSecurity"]
    for c in [*pod["containers"], *pod.get("initContainers", [])]:
        assert c["securityContext"] == spec["containerSecurity"], c["name"]
    for v in pod["volumes"]:
        if "secret" in v:
            assert v["secret"].get("defaultMode") == spec["secretMode"]
        if "configMap" in v:
            assert "defaultMode" not in v["configMap"] and "items" not in v["configMap"]


# -- "Set by": each claim on docs/reference/configuration.md, held to a render


def _good_fixture():
    from htrflow_converter.parse import load

    fixture = ROOT / "packages" / "converter" / "tests" / "fixtures" / "good"
    campaigns, pipelines, cfg = load(
        fixture / "campaigns", fixture / "pipelines", fixture / "converter.yaml"
    )
    return campaigns[0], pipelines["demo-v1"], cfg


def _job(campaign, pipeline, cfg) -> dict:
    from htrflow_converter import render as render_objects

    objects = render_objects.campaign_objects(campaign, pipeline, cfg)
    return next(o for o in objects if o["kind"] == "Job")


def _job_env(job: dict) -> dict[str, str | None]:
    container = job["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


#: A value for each converter.yaml or pipeline key FREE_ENV names that no
#: fixture uses, so a render with it shows where it lands.
_CHANGED = {
    "namespace": "changed-ns",
    "public_results_base": "https://changed.example.org/results",
    "manifest_max_bytes": 1234567,
    "fetch_max_bytes": 7654321,
    "id": "changed-v9",
    "image": f"ghcr.io/example/changed@sha256:{'c' * 64}",
}


def test_every_env_job_shape_leaves_free_has_a_named_setter():
    """render.py fills each `free` env var per campaign. The page says from
    what, and a free var nobody named would be a setting the page could not
    place."""
    assert set(FREE_ENV) == set(job_shape()["batch"]["free"])


#: A `size` one is rendered only for a pipeline that names a size, from
#: that size's key: test_sizes.py holds it to the key it names.
@pytest.mark.parametrize(
    "name", [n for n, (kind, _) in FREE_ENV.items() if kind not in ("fixed", "size")]
)
def test_a_wrapper_env_the_page_says_a_file_sets_is_set_from_it(name: str):
    """ "Set by `converter.yaml` `fetch_max_bytes`" is true only if changing
    that key changes the rendered Job's env, and changes nothing a `fixed`
    one says."""
    kind, key = FREE_ENV[name]
    campaign, pipeline, cfg = _good_fixture()
    before = _job_env(_job(campaign, pipeline, cfg))
    if kind == "converter":
        cfg = cfg.model_copy(update={key: _CHANGED[key]})
    else:
        pipeline = pipeline.model_copy(update={key: _CHANGED[key]})
    after = _job_env(_job(campaign, pipeline, cfg))
    assert str(_CHANGED[key]) in (after[name] or "") != before[name]
    for fixed in (n for n, (k, _) in FREE_ENV.items() if k == "fixed"):
        assert after[fixed] == before[fixed], fixed


def test_a_wrapper_setting_for_a_local_run_only_reaches_no_rendered_job():
    """The page calls a wrapper setting "a local run only" when nothing a
    deployment runs can set it: no rendered Job carries it (campaign or
    warm-up), the prologue does not export it, the image does not bake it,
    and job-shape -- which admits exactly its own env lists -- does not name
    it. Everything else the page must place somewhere real."""
    from htrflow_converter import render as render_objects

    campaign, pipeline, cfg = _good_fixture()
    rendered = set(_job_env(_job(campaign, pipeline, cfg)))
    rendered |= {
        n
        for o in render_objects.pipeline_objects(pipeline, cfg)
        if o["kind"] == "Job"
        for n in _job_env(o)
    }
    # A pipeline at a named size carries what its size renders (B105).
    sized = cfg.model_copy(update={"sizes": {"s": Size(cpu="1", memory="4Gi")}})
    at_size = pipeline.model_copy(update={"size": "s"})
    rendered |= set(_job_env(_job(campaign, at_size, sized)))
    shape = job_shape()["batch"]
    admitted = {*shape["pinned"], *shape["free"], *shape["secretEnv"]}
    admitted |= set(shape["fieldEnv"])
    baked = image_env("htrflow-batch.dockerfile")
    for name in (n for n, _ in _model_rows(SURFACES[0][3])):
        local = wrapper_set_by(name).startswith(LOCAL_ONLY)
        assert local == (name not in rendered | set(script_exports()) | baked), name
        assert not (local and name in admitted), name


def test_a_web_setting_the_page_says_the_chart_sets_is_set_by_that_value():
    """Each chart value the page names for a web env var, set to a sentinel,
    is what the rendered Deployment carries; the namespace comes from the
    downward API; a local-run setting is not in the Deployment at all."""
    names = [n for n, _ in _model_rows(SURFACES[1][3])]
    plain = _rendered("Deployment", "htrflow-web")
    env = plain["spec"]["template"]["spec"]["containers"][0]["env"]
    by_name = {e["name"]: e for e in env}
    for name, paths in chart_web_env().items():
        assert name in names, name
        if not paths:
            assert by_name[name]["valueFrom"] == {
                "fieldRef": {"fieldPath": "metadata.namespace"}
            }
            continue
        sentinel = f"https://{name.lower().replace('_', '-')}.example.org"
        web = _rendered("Deployment", "htrflow-web", f"{paths[0]}={sentinel}")
        env = web["spec"]["template"]["spec"]["containers"][0]["env"]
        assert {e["name"]: e.get("value") for e in env}[name] == sentinel, name
    for name in names:
        if web_set_by(name).startswith(LOCAL_ONLY):
            assert name not in by_name, name


def test_every_image_env_the_page_explains_is_one_an_image_sets():
    baked = image_env("htrflow-batch.dockerfile") | image_env("htrflow-web.dockerfile")
    assert set(IMAGE_ENV_DOC) <= baked


@pytest.mark.parametrize(
    "model,key", overrides(), ids=lambda v: getattr(v, "__name__", v)
)
def test_a_campaign_or_pipeline_key_the_page_says_overrides_converter_yaml_does(
    model, key
):
    """The page says a pipeline's `max_seconds:` overrides converter.yaml's,
    and a campaign's `window:` may lower it: rendered, the Job shows it."""
    from htrflow_converter.models import Pipeline

    where = {
        "max_seconds": ("spec", "template", "spec", "activeDeadlineSeconds"),
        "ttl_seconds_after_finished": ("spec", "ttlSecondsAfterFinished"),
        "window": ("spec", "parallelism"),
    }
    assert key in where, f"say how the page's claim about `{key}:` is checked"
    campaign, pipeline, cfg = _good_fixture()
    ours = getattr(cfg, key) - 1
    if model is Pipeline:
        pipeline = pipeline.model_copy(update={key: ours})
    else:
        campaign = campaign.model_copy(update={key: ours})
    node = _job(campaign, pipeline, cfg)
    for step in where[key]:
        node = node[step]
    assert node == ours


def test_the_frontend_build_settings_are_the_ones_config_ts_reads():
    """The page lists the VITE_ variables frontend/src/lib/config.ts reads
    and says the published image builds with none of them set."""
    assert [n for n, _ in frontend_rows()] == list(FRONTEND_DOC)
    dockerfile = (ROOT / ".docker" / "htrflow-web.dockerfile").read_text("utf-8")
    assert "VITE_" not in dockerfile
