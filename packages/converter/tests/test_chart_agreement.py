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

import re
import sys
from pathlib import Path

import pytest
import yaml

from htrflow_converter.models import ConverterConfig

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
    LIST_AGREEMENTS,
    PAGE,
    SECURITY,
    SURFACES,
    WEB_DEFAULT_DOC,
    _chart_rows,
    _model_rows,
    render,
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _at(values: dict, path: str) -> object:
    for key in path.split("."):
        values = values[key]
    return values


def _disagreements(config: dict) -> list[str]:
    values = _load(CHART / "values.yaml")
    # A key a converter.yaml leaves out is the converter's default.
    config = {f: config.get(f, getattr(ConverterConfig(), f)) for f, _ in AGREEMENTS}
    return [
        f"`{field}` is {config[field]!r} but the chart's `{path}` is "
        f"{_at(values, path)!r} — they name one cluster object"
        for field, path in AGREEMENTS
        if config[field] != _at(values, path)
    ]


def test_converter_defaults_agree_with_the_chart_defaults():
    defaults = {f: getattr(ConverterConfig(), f) for f, _ in AGREEMENTS}
    assert _disagreements(defaults) == []


def test_the_example_campaigns_repo_agrees_with_the_chart_defaults():
    assert _disagreements(_load(EXAMPLE)) == []


def test_the_results_base_reaches_both_of_its_consumers():
    """One value under four names: `publicResultsBase` (chart) reaches the web
    front as `HTRFLOW_PUBLIC_RESULTS_BASE` and the wrapper as
    `PUBLIC_RESULTS_BASE`, which the converter fills from
    `converter.yaml`'s `public_results_base`. Renaming any one of the four
    silently strands a consumer, so the chain is asserted end to end."""
    web = (CHART / "templates" / "web.yaml").read_text(encoding="utf-8")
    assert "name: HTRFLOW_PUBLIC_RESULTS_BASE" in web
    assert ".Values.publicResultsBase" in web
    job_env = _load(JOB_SKELETON)["spec"]["template"]["spec"]["containers"][0]["env"]
    assert "PUBLIC_RESULTS_BASE" in [e["name"] for e in job_env]
    render_src = (CONVERTER_SRC / "render.py").read_text(encoding="utf-8")
    pattern = r'"PUBLIC_RESULTS_BASE"\s*:\s*cfg\.public_results_base'
    assert re.search(pattern, render_src)


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


def test_the_read_api_may_write_the_campaign_record():
    """The read API writes one object: the per-campaign status ConfigMap
    (B76). `create` goes with `patch` because a server-side apply of an
    object that is not there yet is a create; nothing grants a delete."""
    web = (CHART / "templates" / "web.yaml").read_text(encoding="utf-8")
    rules = web.split("rules:", 1)[1].split("---", 1)[0]
    verbs = dict(re.findall(r'resources: \["(\w+)"\]\n    verbs: \[([^\]]*)\]', rules))
    assert verbs["configmaps"].replace('"', "").split(", ") == [
        "get",
        "list",
        "create",
        "patch",
    ]
    assert verbs["jobs"] == verbs["pods"] == '"get", "list"'
    assert not any("watch" in granted for granted in verbs.values()), (
        "the read API computes every response from a get or a list; nothing "
        "in packages/web opens a watch (2026-09-14 audit)"
    )
    assert not any("delete" in granted for granted in verbs.values())


def test_the_apply_identity_may_read_what_it_decides_on():
    """`htrflow-campaigns apply` reads each campaign's live Job by name to
    record how it ended, and reads the record back to leave a finished
    campaign alone (B76). `list` does not authorize a read by name."""
    rbac = (CHART / "templates" / "apply-rbac.yaml").read_text(encoding="utf-8")
    verbs = dict(re.findall(r'resources: \["(\w+)"\]\n    verbs: \[([^\]]*)\]', rbac))
    for resource in ("jobs", "configmaps"):
        granted = verbs[resource].replace('"', "").split(", ")
        assert granted[:1] == ["get"], resource
        assert {"create", "patch"} <= set(granted), resource


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
    defaults = {f: getattr(ConverterConfig(), f) for f, _, _ in LIST_AGREEMENTS}
    for pair, (ours, theirs) in _names(defaults).items():
        assert ours == theirs, pair
    for pair, (ours, theirs) in _names(_load(EXAMPLE)).items():
        assert ours == theirs, pair


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


#: The Kubernetes client classes cluster.py builds, by the API group their
#: methods reach. A class not listed fails the test by name: add its group.
_CLIENT_GROUPS = {
    "CoreV1Api": "",
    "BatchV1Api": "batch",
    "CoordinationV1Api": "coordination.k8s.io",
    "CustomObjectsApi": None,  # the group is an argument of each call
}
#: A client method's verb -> the RBAC verb the API server checks.
_RBAC_VERB = {
    "read": "get",
    "list": "list",
    "create": "create",
    "patch": "patch",
    "replace": "update",
    "delete": "delete",
}
_CLIENT_METHOD = re.compile(r"(read|list|create|patch|replace|delete)_namespaced_(\w+)")


def _calls_cluster_py_makes() -> set[tuple[str, str, str]]:
    """(API group, resource, RBAC verb) for every call cluster.py makes,
    read off its syntax tree: `self._method(kind, verb)` through `_KINDS`,
    and every `self.<client>.<verb>_namespaced_<noun>` it calls or hands to
    a retry helper. A server-side apply (`_content_type=APPLY_PATCH`) of an
    object that does not exist yet is authorized as a create as well."""
    import ast

    from htrflow_converter import cluster

    tree = ast.parse((CONVERTER_SRC / "cluster.py").read_text(encoding="utf-8"))
    parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    clients: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.targets[0], ast.Attribute)
        ):
            clients[node.targets[0].attr] = node.value.func.attr

    def group_of(client_attr: str) -> str | None:
        cls = clients[client_attr]
        assert cls in _CLIENT_GROUPS, f"cluster.py uses {cls}: add its API group"
        return _CLIENT_GROUPS[cls]

    def module_value(node: ast.AST) -> object:
        return getattr(cluster, node.id, None) if isinstance(node, ast.Name) else None

    def applies(node: ast.AST) -> bool:
        call = parent.get(node)
        return isinstance(call, ast.Call) and any(
            k.arg == "_content_type"
            and isinstance(k.value, ast.Name)
            and k.value.id == "APPLY_PATCH"
            for k in call.keywords
        )

    found: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_method"
        ):
            kind, verb = node.args[0], node.args[1].value
            kinds = (
                [kind.value] if isinstance(kind, ast.Constant) else list(cluster._KINDS)
            )
            for k in kinds:
                api, noun = cluster._KINDS[k]
                resource = noun.replace("_", "") + "s"
                found.add((group_of(api), resource, _RBAC_VERB[verb]))
                if verb == "patch" and applies(node):
                    found.add((group_of(api), resource, "create"))
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and (m := _CLIENT_METHOD.fullmatch(node.attr))
        ):
            verb, noun = m.groups()
            group = group_of(node.value.attr)
            resource = noun.replace("_", "") + "s"
            if noun == "custom_object":
                call = parent[node]
                args = [
                    module_value(a.value if isinstance(a, ast.Starred) else a)
                    for a in call.args
                ]
                group = next(a[0] for a in args if isinstance(a, tuple))
                resource = next(a for a in args if isinstance(a, str))
            found.add((group, resource, _RBAC_VERB[verb]))
    return found


def _apply_role_grants() -> list[dict]:
    text = (CHART / "templates" / "apply-rbac.yaml").read_text(encoding="utf-8")
    role = text.split("kind: Role\n", 1)[1].split("---", 1)[0]
    return yaml.safe_load("rules:" + role.split("\nrules:", 1)[1])["rules"]


def test_the_apply_role_grants_every_call_cluster_py_makes():
    """`htrflow-campaigns apply` run in the cluster has only this Role: a
    call it makes that the Role does not grant is a 403 halfway through an
    apply -- a Lease it cannot take fails every run closed. The calls are
    read off cluster.py itself, so a new one fails here before a cluster
    sees it. A create is never scoped by name in RBAC, so it must come from
    a rule without `resourceNames`."""
    calls = _calls_cluster_py_makes()
    rules = _apply_role_grants()
    granted = {
        (g, r, v)
        for rule in rules
        for g in rule["apiGroups"]
        for r in rule["resources"]
        for v in rule["verbs"]
    }
    assert {
        ("batch", "jobs", "delete"),
        ("kueue.x-k8s.io", "workloads", "patch"),
    } <= calls
    assert calls - granted == set()
    for group, resource, verb in calls:
        if verb == "create":
            assert any(
                group in r["apiGroups"]
                and resource in r["resources"]
                and "create" in r["verbs"]
                and "resourceNames" not in r
                for r in rules
            ), (group, resource)


def _job_shape_spec() -> dict:
    policy = (CHART / "templates" / "policies" / "job-shape.yaml").read_text(
        encoding="utf-8"
    )
    return yaml.safe_load(re.search(r"\{\{- \$spec := `([^`]*)`", policy).group(1))


@pytest.mark.parametrize(
    "role,skeleton,added",
    [
        ("batch", "campaign-job.yaml", set()),
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
    for name in shape["free"]:
        assert "valueFrom" not in env[name], name
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
