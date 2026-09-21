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

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from htrflow_converter.models import _NAME_RE

REPO = Path(__file__).resolve().parents[3]
CHART = REPO / "charts" / "htrflow-batch"
NAMESPACE = "htr-batch"

#: Values the chart `fail`s without and that no cluster is present to look
#: up. Mirrors the Makefile's CHART_DEFAULT_SETS -- never an install.
REQUIRED_SETS = (
    "publicResultsBase=https://x/",
    "network.apiServer.cidr=10.16.51.10/32",
    "web.image=docker.io/riksarkivet/htrflow-web@sha256:" + "0" * 64,
)
#: The default ingress list is a catch-all, and the chart makes that an
#: explicit choice rather than a silent default.
PUBLIC_INGRESS = "network.web.allowPublicIngress=true"
DEFAULT_SETS = REQUIRED_SETS + (PUBLIC_INGRESS,)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH")


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
    # A policy that refuses a legal name is an outage, not a control: the
    # status write would fail for that campaign and nothing would say why.
    assert _status_name_pattern(web) == f"^campaign-{_NAME_RE.pattern[:-2]}-status$"
    assert policy["spec"]["failurePolicy"] == "Fail"


def _status_name_pattern(rule_body: dict) -> str:
    key = rule_body["validate"]["deny"]["conditions"]["all"][0]["key"]
    return re.search(r"regex_match\('([^']+)'", key).group(1)


@pytest.mark.parametrize(
    "campaign",
    ["kyrk", "sdhk.1500", "a", "kyrk-1600-1700", "sdhk.1500.b-2"],
)
def test_every_campaign_name_the_converter_accepts_may_have_a_status(
    full: list[dict], campaign: str
):
    """The converter's own name rule allows dots (`_NAME_RE`), and campaign
    files are named after their archive references -- `sdhk.1500` is the
    shape, not the exception. The first pattern here was a DNS *label* and
    refused every dotted one, so with the policies on the read API's write
    would have been denied for exactly the campaigns most likely to exist.
    The rule mirrors `_NAME_RE` instead of approximating it.
    """
    assert _NAME_RE.match(campaign), "fixture is not a name the converter takes"
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    pattern = _status_name_pattern(rule(policy, "web-writes-status-only"))
    assert re.match(pattern, f"campaign-{campaign}-status")


@pytest.mark.parametrize("name", ["htr-pipeline-demo-v1", "campaign-kyrk", "x-status"])
def test_the_objects_the_rule_exists_to_protect_are_still_refused(
    full: list[dict], name: str
):
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    pattern = _status_name_pattern(rule(policy, "web-writes-status-only"))
    assert not re.match(pattern, name)


def test_the_rbac_scope_policy_follows_the_policies_switch(default: list[dict]):
    """Every ClusterPolicy this chart ships is behind
    `security.policies.enabled`, because a policy nothing reconciles is
    worse than none at all."""
    assert objects(default, "ClusterPolicy") == []


# --- D2: an unauthenticated NodePort open to every address by default -----


def test_the_catch_all_web_ingress_has_to_be_said_out_loud():
    """`network.web.ingressCidrs` defaults to every IPv4 address, in front of
    a NodePort with no authentication of its own. The default stays -- the
    dev stack and the compose smoke both rely on it, and narrowing it by
    guess would break them on upgrade -- but it stops being something an
    operator can ship without noticing."""
    refused = helm_template(sets=REQUIRED_SETS)
    assert refused.returncode != 0
    assert "network.web.allowPublicIngress" in refused.stderr

    allowed = render(sets=DEFAULT_SETS)
    ingress = named(allowed, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "0.0.0.0/0"}}]


def test_a_named_ingress_range_needs_no_opt_out():
    """The flag is about the catch-all, not about ingress: an operator who
    lists the ranges that may reach the web front says enough by listing
    them."""
    rendered = render(sets=REQUIRED_SETS + ("network.web.ingressCidrs={10.16.0.0/16}",))
    ingress = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "10.16.0.0/16"}}]


@pytest.fixture
def empty_ingress(tmp_path: Path) -> Path:
    """`--set` cannot spell an empty list, so the operator's values file."""
    path = tmp_path / "empty-ingress.yaml"
    path.write_text("network:\n  web:\n    ingressCidrs: []\n", encoding="utf-8")
    return path


def test_an_empty_ingress_list_is_refused_not_opened(empty_ingress: Path):
    """An ingress rule with an empty `from` matches every source, so
    `ingressCidrs: []` -- what an operator writes to shut the web front --
    rendered exactly the catch-all the guard refuses, without the guard
    noticing (finding 3100). It fails with a sentence that says so."""
    refused = helm_template(values=str(empty_ingress), sets=REQUIRED_SETS)
    assert refused.returncode != 0
    assert "network.web.ingressCidrs is empty" in refused.stderr
    assert "network.web.allowPublicIngress" in refused.stderr


def test_an_empty_ingress_list_with_the_opt_in_is_the_catch_all_it_renders(
    empty_ingress: Path,
):
    """With the flag the operator has accepted every address; the policy
    says so in the same words the catch-all does."""
    rendered = render(values=str(empty_ingress), sets=DEFAULT_SETS)
    ingress = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "0.0.0.0/0"}}]


@pytest.mark.parametrize(
    "cidrs",
    ["{0.0.0.0/1,128.0.0.0/1}", "{10.16.0.0/16,0.0.0.0/7}", "{64.0.0.0/2}"],
)
def test_a_catch_all_split_into_halves_is_still_a_catch_all(cidrs: str):
    """The guard compared strings, so `0.0.0.0/1` + `128.0.0.0/1` -- every
    address, in two entries -- passed it (finding 3064). Anything wider than
    a /8 is somebody's whole internet and needs the same opt-in."""
    refused = helm_template(sets=REQUIRED_SETS + (f"network.web.ingressCidrs={cidrs}",))
    assert refused.returncode != 0
    assert "wider than /8" in refused.stderr
    assert "network.web.allowPublicIngress" in refused.stderr

    accepted = render(sets=DEFAULT_SETS + (f"network.web.ingressCidrs={cidrs}",))
    assert named(accepted, "NetworkPolicy", "htr-web")


def test_a_private_block_is_narrow_enough():
    """A /8 is the widest range an operator can name without the flag -- the
    `10.0.0.0/8` a site network commonly is."""
    rendered = render(sets=REQUIRED_SETS + ("network.web.ingressCidrs={10.0.0.0/8}",))
    ingress = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "10.0.0.0/8"}}]


def test_the_web_front_sees_its_clients_own_addresses(default: list[dict]):
    """With `externalTrafficPolicy: Cluster` a NodePort connection is SNAT'd
    to the node before the pod sees it, so the ingress list only ever
    matched node addresses -- and the docs told operators to list the node
    range, which every client reaching a node then matched (finding 3064).
    `Local` keeps the client's address, so the list restricts clients."""
    service = named(default, "Service", "htrflow-web")
    assert service["spec"]["type"] == "NodePort"
    assert service["spec"]["externalTrafficPolicy"] == "Local"


def test_the_refusal_no_longer_advises_listing_the_node_range():
    """Listing the node range is what defeated the list; the chart's own
    sentence must not tell anyone to do it."""
    refused = helm_template(sets=REQUIRED_SETS)
    assert "node range" not in refused.stderr
    assert "SNAT" not in refused.stderr


def test_the_catch_all_guard_is_silent_when_the_policies_are_not_rendered():
    """A campaigns repo's CI renders this chart with `network.enabled=false`
    to get at the policy objects alone. There is no web NetworkPolicy in
    that render, so there is nothing for the guard to warn about."""
    result = helm_template(sets=REQUIRED_SETS + ("network.enabled=false",))
    assert result.returncode == 0, result.stderr


# --- D3: what a catch-all egress still reaches ----------------------------

#: k3s pod + service ranges (the chart's `clusterCidrs` default), loopback,
#: the link-local block every cloud serves instance credentials from, and
#: the three private ranges a VPC is built out of.
CATCH_ALL_EXCEPT = {
    "10.42.0.0/16",
    "10.43.0.0/16",
    "169.254.0.0/16",
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
}


def _catch_alls(policy: dict) -> list[dict]:
    return [
        to["ipBlock"]
        for rule in policy["spec"]["egress"]
        for to in rule.get("to", [])
        if to.get("ipBlock", {}).get("cidr") == "0.0.0.0/0"
    ]


@pytest.mark.parametrize("policy_name", ["htr-batch-job", "htr-warmup"])
def test_a_catch_all_egress_reaches_neither_metadata_nor_a_private_network(
    policy_name: str,
):
    """`except` used to carve out the pod, service and node ranges and
    nothing else, so a pod with the documented catch-all could still reach
    169.254.169.254 -- the address a cloud hands out instance credentials
    on -- and every RFC1918 address in the surrounding network. Warm-up pods
    have that catch-all by construction (Hugging Face Hub is a CDN with no
    CIDR to pin); batch Jobs get it whenever a volume's images live off the
    IIIF origin, which the deployment page documents.
    """
    rendered = render(sets=DEFAULT_SETS + ("network.iiifCidrs={0.0.0.0/0}",))
    blocks = _catch_alls(named(rendered, "NetworkPolicy", policy_name))
    assert blocks, f"{policy_name} has no catch-all egress to check"
    for block in blocks:
        assert set(block["except"]) == CATCH_ALL_EXCEPT


def test_a_private_range_the_operator_listed_is_still_reachable():
    """The carve-out is of the catch-all, not of the address: an explicitly
    listed range is its own ipBlock in the same rule, and egress rules are a
    union. So an on-premises IIIF host keeps working by being named."""
    rendered = render(
        sets=DEFAULT_SETS + ("network.iiifCidrs={0.0.0.0/0,10.1.2.3/32}",)
    )
    policy = named(rendered, "NetworkPolicy", "htr-batch-job")
    targets = [to for rule in policy["spec"]["egress"] for to in rule.get("to", [])]
    assert {"ipBlock": {"cidr": "10.1.2.3/32"}} in targets


# --- D4: all-port egress to the S3 range ----------------------------------


@pytest.mark.parametrize("policy_name", ["htr-batch-job", "htr-web"])
def test_s3_egress_names_the_ports_it_needs(policy_name: str):
    """The CIDR half of the S3 rule carried no `ports` at all, so both pods
    that reach S3 had egress to EVERY port of that range -- which for a
    self-hosted endpoint is a range of the operator's own network. The
    in-cluster half always named 9000; this is the other half saying so too.
    """
    rendered = render(sets=DEFAULT_SETS + ("network.s3Cidrs={10.0.0.5/32}",))
    egress = named(rendered, "NetworkPolicy", policy_name)["spec"]["egress"]
    by_cidr = [
        rule
        for rule in egress
        if {"ipBlock": {"cidr": "10.0.0.5/32"}} in rule.get("to", [])
    ]
    assert len(by_cidr) == 1
    assert by_cidr[0]["ports"] == [{"port": 443}]

    in_cluster = [
        rule
        for rule in egress
        if {"podSelector": {"matchLabels": {"app": "rustfs"}}} in rule.get("to", [])
    ]
    assert len(in_cluster) == 1
    assert in_cluster[0]["ports"] == [{"port": 9000}]


def test_an_endpoint_on_another_port_is_a_value_not_a_fork():
    """A self-hosted endpoint is often not on 443, and the answer to that
    must not be "widen the rule again"."""
    rendered = render(
        sets=DEFAULT_SETS
        + ("network.s3Cidrs={10.0.0.5/32}", "network.s3Ports={9000,443}")
    )
    egress = named(rendered, "NetworkPolicy", "htr-batch-job")["spec"]["egress"]
    by_cidr = next(
        rule
        for rule in egress
        if {"ipBlock": {"cidr": "10.0.0.5/32"}} in rule.get("to", [])
    )
    assert by_cidr["ports"] == [{"port": 9000}, {"port": 443}]


# --- D5: a label is not what makes a ConfigMap a pipeline -----------------


def test_the_model_revision_rule_reaches_any_configmap_carrying_a_pipeline(
    full: list[dict],
):
    """The rule matched `managed-by: converter`, a label anyone who can
    create a ConfigMap can leave off. The rule exists because unpinned
    Hugging Face weights are mutable pickles, and a hand-written pipeline
    ConfigMap is exactly the case it should catch. What makes a ConfigMap a
    pipeline is the `pipeline.yaml` key, so that is what it matches on."""
    policy = named(full, "ClusterPolicy", f"htrflow-batch-model-revision-{NAMESPACE}")
    pinned = rule(policy, "pipeline-models-pinned")
    resources = pinned["match"]["any"][0]["resources"]
    assert resources["kinds"] == ["ConfigMap"]
    assert resources["namespaces"] == [NAMESPACE]
    assert "selector" not in resources
    assert 'data."pipeline.yaml"' in pinned["context"][0]["variable"]["jmesPath"]


# --- D6: the apply identity's delete is namespace-wide --------------------


def test_the_apply_identity_may_only_delete_what_the_converter_rendered(
    full: list[dict],
):
    """`--prune` is a delete, so the Role grants one -- and RBAC grants it
    over the whole resource type: every Job and every ConfigMap in the
    namespace, a running campaign's Job and another team's ConfigMap
    included. The prune itself only ever selects converter-labelled objects;
    this makes that the limit rather than the intention."""
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    prune = rule(policy, "apply-deletes-only-what-it-rendered")
    match = prune["match"]["any"][0]
    assert sorted(match["resources"]["kinds"]) == ["ConfigMap", "Job"]
    assert match["resources"]["operations"] == ["DELETE"]
    assert match["subjects"] == [
        {"kind": "ServiceAccount", "name": "htrflow-campaigns", "namespace": NAMESPACE}
    ]
    condition = prune["validate"]["deny"]["conditions"]["all"][0]
    # A DELETE admission review carries the object as `oldObject`; reading
    # `request.object` there would compare against nothing at all.
    assert "request.oldObject.metadata.labels" in condition["key"]
    assert "htrflow.riksarkivet.se/managed-by" in condition["key"]
    assert condition["operator"] == "NotEquals"
    assert condition["value"] == "converter"


def test_the_prune_rule_is_rendered_with_the_identity_it_scopes():
    """No ServiceAccount, nothing to scope: `apply.rbac.enabled` is off by
    default because an idle identity that may delete Jobs is a liability."""
    rendered = render(
        sets=DEFAULT_SETS
        + ("security.policies.enabled=true", "apply.rbac.enabled=false")
    )
    policy = named(rendered, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    assert [r["name"] for r in policy["spec"]["rules"]] == ["web-writes-status-only"]


# --- D7: the apply identity had no way out of the default deny ------------


def test_the_apply_pod_can_reach_the_api_server_it_was_given_an_identity_for(
    full: list[dict],
):
    """`apply.rbac.enabled` exists for `htrflow-campaigns apply` running
    INSIDE the cluster. With the namespace default deny on -- which is the
    chart's own default -- that pod had a ServiceAccount and no route to the
    API server, so the apply hung until its deadline with nothing in its log
    to say why. An identity without a network is not an identity."""
    policy = named(full, "NetworkPolicy", "htr-campaigns-apply")
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "htrflow-campaigns"}
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in policy["spec"]

    egress = policy["spec"]["egress"]
    dns = next(r for r in egress if any("podSelector" in to for to in r["to"]))
    assert dns["ports"] == [
        {"port": 53, "protocol": "UDP"},
        {"port": 53, "protocol": "TCP"},
    ]
    api = next(r for r in egress if any("ipBlock" in to for to in r["to"]))
    # ci/full-values.yaml states the endpoint, as `helm template` must.
    assert api["to"] == [{"ipBlock": {"cidr": "10.16.51.56/32"}}]
    assert api["ports"] == [{"port": 6443}]
    # Nothing else: it reads its campaigns from a directory, not a network.
    assert len(egress) == 2


def test_the_apply_pods_policy_comes_with_its_identity():
    """No ServiceAccount, no pod to let out."""
    rendered = render(sets=DEFAULT_SETS + ("apply.rbac.enabled=false",))
    assert [
        o
        for o in objects(rendered, "NetworkPolicy")
        if o["metadata"]["name"] == "htr-campaigns-apply"
    ] == []


# --- 3101: an HA control plane is more than one API server ----------------


def _api_rule(policy: dict) -> dict:
    return next(
        r for r in policy["spec"]["egress"] if any("ipBlock" in to for to in r["to"])
    )


@pytest.mark.parametrize("policy_name", ["htr-web", "htr-campaigns-apply"])
def test_every_api_server_address_is_let_out(policy_name: str):
    """Behind the `kubernetes` ClusterIP sit as many API servers as the
    control plane has, and after DNAT a connection goes to any of them. A
    rule naming one of three drops two calls in three (finding 3101), so
    `network.apiServer.cidrs` names them all, beside the single `cidr`."""
    rendered = render(
        sets=DEFAULT_SETS
        + (
            "apply.rbac.enabled=true",
            "network.apiServer.cidrs={10.16.51.11/32,10.16.51.12/32}",
        )
    )
    api = _api_rule(named(rendered, "NetworkPolicy", policy_name))
    assert api["to"] == [
        {"ipBlock": {"cidr": "10.16.51.10/32"}},
        {"ipBlock": {"cidr": "10.16.51.11/32"}},
        {"ipBlock": {"cidr": "10.16.51.12/32"}},
    ]
    assert api["ports"] == [{"port": 6443}]


def test_the_list_alone_is_enough():
    sets = tuple(
        s for s in REQUIRED_SETS if not s.startswith("network.apiServer.cidr=")
    )
    rendered = render(
        sets=sets + (PUBLIC_INGRESS, "network.apiServer.cidrs={10.16.51.11/32}")
    )
    api = _api_rule(named(rendered, "NetworkPolicy", "htr-web"))
    assert api["to"] == [{"ipBlock": {"cidr": "10.16.51.11/32"}}]


def test_no_api_server_address_at_all_is_still_refused():
    sets = tuple(
        s for s in REQUIRED_SETS if not s.startswith("network.apiServer.cidr=")
    )
    refused = helm_template(sets=sets + (PUBLIC_INGRESS,))
    assert refused.returncode != 0
    assert "network.apiServer.cidrs" in refused.stderr


def _from_endpoints(tmp_path: Path, endpoints: dict) -> dict:
    """Run the chart's own Endpoints reader on a fixture: `helm template`
    has no cluster to `lookup`, so a throwaway chart carries a copy of
    `_helpers.tpl` and one template that calls the helper on a value."""
    chart = tmp_path / "probe"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: probe\nversion: 0.0.0\n", encoding="utf-8"
    )
    shutil.copy(
        CHART / "templates" / "_helpers.tpl", chart / "templates" / "_helpers.tpl"
    )
    (chart / "templates" / "probe.yaml").write_text(
        "result: {{ include "
        '"htrflow-batch.apiServerFromEndpoints" .Values.endpoints }}\n',
        encoding="utf-8",
    )
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"endpoints": endpoints}), encoding="utf-8")
    result = subprocess.run(
        ["helm", "template", "probe", str(chart), "-f", str(values)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return next(d for d in yaml.safe_load_all(result.stdout) if d)["result"]


def test_auto_detection_reads_every_endpoint_address_and_port(tmp_path: Path):
    """The `lookup` path took `index .subsets 0` and `index .addresses 0`:
    one API server of an HA control plane. Every address of every subset,
    and every port, is what the Endpoints object says the Service reaches."""
    endpoints = {
        "subsets": [
            {
                "addresses": [{"ip": "10.16.51.11"}, {"ip": "10.16.51.12"}],
                "ports": [{"name": "https", "port": 6443, "protocol": "TCP"}],
            },
            {
                "addresses": [{"ip": "10.16.51.13"}, {"ip": "10.16.51.11"}],
                "ports": [{"name": "https", "port": 6443, "protocol": "TCP"}],
            },
        ]
    }
    assert _from_endpoints(tmp_path, endpoints) == {
        "cidrs": ["10.16.51.11/32", "10.16.51.12/32", "10.16.51.13/32"],
        "ports": [6443],
    }


@pytest.mark.parametrize(
    "endpoints",
    [{}, {"subsets": []}, {"subsets": [{"ports": [{"port": 6443}]}]}],
)
def test_auto_detection_of_an_endpoints_object_without_addresses_is_empty(
    tmp_path: Path, endpoints: dict
):
    """An Endpoints object with no subsets used to stop the render with
    `index of nil`; it is simply nothing detected, and the caller asks for
    the value instead."""
    assert _from_endpoints(tmp_path, endpoints)["cidrs"] == []


# --- D8: a signing identity nothing ever signed as -------------------------

#: The identity `publish.yml` actually gets from Sigstore: the repository is
#: under the AI- organisation, and the workflow is `workflow_dispatch`, so
#: the certificate carries the branch it ran from -- never a tag ref.
SIGNING_SUBJECT = (
    "https://github.com/AI-Riksarkivet/htrflow-batch"
    "/.github/workflows/publish.yml@refs/heads/main"
)


def test_the_signing_identity_example_is_one_a_release_can_produce(
    full: list[dict],
):
    """Both copies of the cosign subject named the wrong organisation, and
    the render fixture also asked for `@refs/tags/*`. An operator who copies
    either gets a policy that refuses every image the release publishes --
    and finds out at admission, on a cluster, not here."""
    policy = named(full, "ClusterPolicy", f"htrflow-batch-verify-images-{NAMESPACE}")
    keyless = policy["spec"]["rules"][0]["verifyImages"][0]["attestors"][0]
    assert keyless["entries"][0]["keyless"]["subject"] == SIGNING_SUBJECT

    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    assert SIGNING_SUBJECT in values
    assert "github.com/Riksarkivet/" not in values


# --- D10: the container list the image rules walk -------------------------


@pytest.mark.parametrize(
    "policy,rule_name",
    [
        ("images-pinned", "pod-images-pinned"),
        ("images-allowed", "pod-images-allowed"),
    ],
)
def test_the_image_rules_see_an_ephemeral_container_too(
    full: list[dict], policy: str, rule_name: str
):
    """`kubectl debug` attaches an ephemeral container to a running pod, and
    it runs an image of the debugger's choosing on the GPU node, sharing the
    target's namespaces. Both image rules walked `containers` and
    `initContainers` and stopped there, so that image needed neither a
    digest nor an allowed repository.

    Only the Pod rules: Kubernetes forbids `ephemeralContainers` in a pod
    TEMPLATE, so there is nothing for the Job rules to walk.
    """
    rendered = named(full, "ClusterPolicy", f"htrflow-batch-{policy}-{NAMESPACE}")
    pod = rule(rendered, rule_name)["context"][0]["variable"]["jmesPath"]
    assert "[containers, initContainers, ephemeralContainers][]" in pod

    job = rule(rendered, rule_name.replace("pod-", "job-"))
    assert "ephemeralContainers" not in job["context"][0]["variable"]["jmesPath"]


# --- D14: defaults called production-shaped that enforce nothing ----------

#: What `values-prod.yaml` cannot know: the bucket's public base, the API
#: server as pods reach it, the image digest, and who may reach the web
#: front. A profile that guessed any of them would be wrong on every
#: cluster, so they stay the operator's to pass.
PROD_SETS = REQUIRED_SETS + ("network.web.ingressCidrs={10.16.0.0/16}",)


@pytest.fixture(scope="module")
def prod() -> list[dict]:
    return render(values="values-prod.yaml", sets=PROD_SETS)


def test_the_production_profile_turns_on_what_the_defaults_leave_off(
    prod: list[dict],
):
    """`values.yaml` calls itself production-shaped and ships the policies
    off, the allow-list empty, model revisions optional, image verification
    off and Pod Security at baseline -- so an install that follows the page
    enforces none of what this repository built. The profile is where those
    are on, and it renders."""
    policies = {o["metadata"]["name"] for o in objects(prod, "ClusterPolicy")}
    assert policies == {
        f"htrflow-batch-{name}-{NAMESPACE}"
        for name in (
            "images-pinned",
            "images-allowed",
            "model-revision",
            "verify-images",
            "rbac-scope",
        )
    }
    for policy in objects(prod, "ClusterPolicy"):
        assert policy["spec"]["validationFailureAction"] == "Enforce"

    values = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
    assert values["security"]["psaEnforce"] == "restricted"
    assert values["security"]["requireModelRevision"] is True
    assert values["security"]["allowedImageRepos"] == ["docker.io/riksarkivet/"]
    assert values["security"]["verifyImages"]["subject"] == SIGNING_SUBJECT


def test_every_pod_the_profile_renders_passes_pod_security_restricted(
    prod: list[dict],
):
    """`psaEnforce: restricted` is only honest if the pods clear it. This is
    the one pod the chart renders; the campaign and warm-up Jobs are the
    other half, and `test_render.py`'s
    `test_every_job_the_converter_renders_is_restricted_clean` holds them to
    the same shape -- they are applied outside the chart, so no render of it
    can see them."""
    pods = [o["spec"]["template"]["spec"] for o in objects(prod, "Deployment")]
    assert pods
    for spec in pods:
        assert spec["securityContext"]["runAsNonRoot"] is True
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        assert spec["securityContext"]["runAsUser"] >= 1000
        for container in spec["containers"]:
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["readOnlyRootFilesystem"] is True
            assert security["capabilities"] == {"drop": ["ALL"]}


def test_the_profile_leaves_the_site_specific_values_to_the_site():
    """A profile that guessed the results base, the API server address or
    the ingress ranges would be wrong on every cluster. It must fail asking
    for them, not render something plausible."""
    refused = helm_template(values="values-prod.yaml")
    assert refused.returncode != 0


# --- Priority classes: the label the converter renders names a real class -


#: What `values.yaml` ships. A Job with no priority label ranks at 0 in
#: Kueue, and the converter renders no label when a campaign leaves
#: `priority:` out, so the normal class sits at 0: naming it is the same as
#: not naming one. Anything that should wait behind those goes below.
DEFAULT_PRIORITY_CLASSES = {"htr-interactive": 1000, "htr-bulk": 0, "htr-idle": -10}


def test_the_default_render_ships_the_three_priority_classes(default: list[dict]):
    """`render.py` has always put `kueue.x-k8s.io/priority-class: <name>`
    on a Job whose campaign sets `priority:`, and no class of that name
    existed -- which Kueue does not refuse: no Workload, no event, and the
    Job reads "Queued" for ever. The chart is where the names live;
    `converter.yaml`'s `priority_classes` mirrors them so `validate` can
    refuse a name the cluster does not have."""
    classes = objects(default, "WorkloadPriorityClass")
    assert {c["metadata"]["name"]: c["value"] for c in classes} == (
        DEFAULT_PRIORITY_CLASSES
    )
    queue_versions = {o["apiVersion"] for o in objects(default, "ClusterQueue")}
    for cls in classes:
        # Cluster-scoped, and on the API version the queue objects use: a
        # class on an older version than the queue is the pause bug again.
        assert "namespace" not in cls["metadata"]
        assert {cls["apiVersion"]} == queue_versions
        assert cls["description"]
        assert cls["metadata"]["labels"]["app.kubernetes.io/name"] == "htrflow-batch"


def test_preemption_stays_off_with_the_classes_present(default: list[dict]):
    """A class decides who is admitted next, not who is evicted. Turning
    preemption on would stop a running volume mid-transcription, and that
    is a product decision the classes do not make on their own."""
    queue = named(default, "ClusterQueue", "htr-batch-cq")
    assert "preemption" not in queue["spec"]


def test_an_empty_class_list_renders_none():
    """An operator who manages the classes elsewhere (or wants none) empties
    the list, and the chart renders nothing rather than a class with an
    empty name."""
    rendered = render(sets=DEFAULT_SETS + ("queue.priorityClasses=null",))
    assert objects(rendered, "WorkloadPriorityClass") == []
    # The queue itself is untouched by the list being empty.
    named(rendered, "ClusterQueue", "htr-batch-cq")
