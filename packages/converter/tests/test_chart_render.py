"""What `helm template charts/htrflow-batch` actually produces, parsed.

test_chart_agreement.py reads the template *files* -- enough for a name that
two sides must spell the same way, useless for anything a condition or a
loop decides. These tests render the chart the way CI and the chart README
do and assert on the objects that come out, which is the only way to check
that a NetworkPolicy's `except` list carves out what it claims to, or that a
policy appears on one values file and not on another.

Three renders, each the same command an operator would run:

* `default` -- chart defaults plus the three values the chart refuses to
  render without and no cluster is here to `lookup` (the chart README's own
  command).
* `full` -- `ci/full-values.yaml`, every optional feature on.
* `prod` -- `values-prod.yaml`, the profile the deployment page starts from.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CHART = REPO / "charts" / "htrflow-batch"
NAMESPACE = "htr-batch"

#: Values the chart `fail`s without and that no cluster is present to look
#: up. Mirrors the Makefile's CHART_DEFAULT_SETS -- never an install.
DEFAULT_SETS = (
    "publicResultsBase=https://x/",
    "network.apiServer.cidr=10.16.51.10/32",
    "web.image=docker.io/riksarkivet/htrflow-web@sha256:" + "0" * 64,
)

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm not on PATH"
)


def helm_template(
    *, values: str | None = None, sets: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    """Run `helm template` and hand back the result, failure included: the
    chart's guards are as much a part of it as its objects."""
    cmd = ["helm", "template", "htr", str(CHART), "-n", NAMESPACE]
    if values:
        cmd += ["-f", str(CHART / values)]
    for setting in sets:
        cmd += ["--set", setting]
    return subprocess.run(cmd, capture_output=True, text=True)


def render(*, values: str | None = None, sets: tuple[str, ...] = ()) -> list[dict]:
    result = helm_template(values=values, sets=sets)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def objects(rendered: list[dict], kind: str) -> list[dict]:
    return [o for o in rendered if o["kind"] == kind]


def named(rendered: list[dict], kind: str, name: str) -> dict:
    found = [o for o in objects(rendered, kind) if o["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: found {len(found)}"
    return found[0]


def rule(policy: dict, name: str) -> dict:
    return next(r for r in policy["spec"]["rules"] if r["name"] == name)


@pytest.fixture(scope="module")
def default() -> list[dict]:
    return render(sets=DEFAULT_SETS)


@pytest.fixture(scope="module")
def full() -> list[dict]:
    return render(values="ci/full-values.yaml")


# --- D1: the read API's write reaches every ConfigMap in the namespace -----


def test_the_read_api_may_only_write_its_own_status_configmaps(full: list[dict]):
    """A Role grants verbs over a resource *type*: there is no way to say
    "these ConfigMaps". So `create`/`patch` on configmaps for the read API
    also covers `htr-pipeline-<id>`, the immutable ConfigMap a campaign Job
    mounts as its pipeline -- overwrite it and the next campaign loads
    weights of someone else's choosing. Admission is the only place that
    sees who is asking, so the name scope lives there.
    """
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    assert policy["spec"]["validationFailureAction"] == "Enforce"
    # A background scan has no requesting user, so a subject-matched rule
    # cannot run as one; saying so here keeps the two from drifting apart.
    assert policy["spec"]["background"] is False

    web = rule(policy, "web-writes-status-only")
    match = web["match"]["any"][0]
    assert match["resources"]["kinds"] == ["ConfigMap"]
    assert match["resources"]["namespaces"] == [NAMESPACE]
    # Every write: a `patch` arrives as UPDATE, a server-side apply of an
    # absent object as CREATE.
    assert sorted(match["resources"]["operations"]) == ["CREATE", "UPDATE"]
    assert match["subjects"] == [
        {"kind": "ServiceAccount", "name": "htrflow-web", "namespace": NAMESPACE}
    ]
    condition = web["validate"]["deny"]["conditions"]["all"][0]
    assert "campaign-" in condition["key"] and "-status$" in condition["key"]
    assert condition["operator"] == "Equals" and condition["value"] is False


def test_the_rbac_scope_policy_follows_the_policies_switch(default: list[dict]):
    """Every ClusterPolicy this chart ships is behind
    `security.policies.enabled`, because a policy nothing reconciles is
    worse than none at all."""
    assert objects(default, "ClusterPolicy") == []
