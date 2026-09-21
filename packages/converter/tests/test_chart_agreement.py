"""The converter and charts/htrflow-batch agree by naming convention only.

Neither side can see the other: the chart creates the queue, the S3 Secret
and the model-cache PVC, and the converter renders Jobs that *reference*
them by name. Until this test they agreed because two files said the same
word, and `examples/campaigns/converter.yaml` asked a human to keep it that
way in a comment. A rename on one side now fails here.

Only keys both sides have are checked: `namespace` is the release namespace
(a `helm -n` argument, not a value), `runtime_class` has no chart key at all,
and neither has `hf_token_secret` — the Hub-token Secret is the operator's
own object, like the S3 one, and no chart template creates or reads it, so
there is no twin to drift from. docs/reference/configuration.md lists what
is one-sided.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
    CONVERTER_SRC / "template" / ".github" / "workflows" / "render.yml",
]


def _kyverno_step(workflow: Path) -> str:
    steps = _load(workflow)["jobs"]["policy"]["steps"]
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
