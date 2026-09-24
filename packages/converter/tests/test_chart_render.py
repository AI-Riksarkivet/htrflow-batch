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

from htrflow_converter.models import _DNS_LABEL_RE, Campaign

REPO = Path(__file__).resolve().parents[3]
CHART = REPO / "charts" / "htrflow-batch"
DEVSTACK_CHART = REPO / "charts" / "htrflow-devstack"
NAMESPACE = "htr-batch"

#: Values the chart `fail`s without and that no cluster is present to look
#: up: ci/default-values.yaml, the one copy the Makefile and `dagger call
#: check-chart` render with too. Every render of this chart starts from it
#: (helm_template), so a test adds only what it is about.
REQUIRED_VALUES = "ci/default-values.yaml"
REQUIRED_SETS: tuple[str, ...] = ()
#: The default ingress list is a catch-all, and the chart makes that an
#: explicit choice rather than a silent default.
PUBLIC_INGRESS = "network.web.allowPublicIngress=true"
#: So is an install that enforces nothing: the chart defaults leave the
#: Kyverno policies off, and a render that keeps them off says so.
POLICIES_OFF = "security.policies.allowDisabled=true"
DEFAULT_SETS = REQUIRED_SETS + (PUBLIC_INGRESS, POLICIES_OFF)

#: Two refusals several tests look for, verbatim: the chart's own sentence
#: is what an operator reads, so a test that only saw a non-zero exit could
#: not tell one guard from another (finding 3103).
S3_NOWHERE_REFUSAL = (
    "network.s3Cidrs is empty and network.s3InNamespace is false, so campaign"
    " pods and the web front have no route to the results bucket and every"
    " volume would fail after its GPU time: list the S3 endpoint's ranges in"
    " network.s3Cidrs, or set network.s3InNamespace=true when the bucket is the"
    " in-namespace RustFS of charts/htrflow-devstack"
)
CLUSTER_CIDRS_REFUSAL = (
    "network.clusterCidrs is empty, so no egress range the chart renders would"
    " carve the cluster's own pod and service ranges out of itself: list your"
    " cluster's pod and service CIDRs"
)
IIIF_NOWHERE_REFUSAL = (
    "network.iiifCidrs is empty and has no default: name the address ranges of"
    " the IIIF servers your campaigns fetch page images from, e.g. --set"
    " network.iiifCidrs='{<cidr>}' (0.0.0.0/0 admits any origin and still"
    " reaches no cluster or private address)"
)
RESULTS_BASE_REFUSAL = (
    "publicResultsBase is required (the read API serves S3 links built from it)"
)
API_SERVER_REFUSAL = (
    "network.apiServer.cidr or network.apiServer.cidrs is required when the"
    " kube-apiserver endpoints cannot be looked up (helm template / no RBAC);"
    " list every API server of an HA control plane"
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH")


def helm_template(
    *,
    values: str | tuple[str, ...] | None = None,
    sets: tuple[str, ...] = (),
    chart: Path = CHART,
    required: str = REQUIRED_VALUES,
) -> subprocess.CompletedProcess[str]:
    """Run `helm template` and hand back the result, failure included: the
    chart's guards are as much a part of it as its objects. This chart's
    renders start from `required` (REQUIRED_VALUES); `values` files come after it (later
    ones win), then `--set`, and a `json:` setting is a `--set-json` -- the
    one way to say an empty list on the command line."""
    cmd = ["helm", "template", "htr", str(chart), "-n", NAMESPACE]
    files = (values,) if isinstance(values, str) else values or ()
    if chart == CHART:
        files = (required, *files)
    for f in files:
        cmd += ["-f", str(chart / f)]
    for setting in sets:
        if setting.startswith("json:"):
            cmd += ["--set-json", setting.removeprefix("json:")]
        else:
            cmd += ["--set", setting]
    return subprocess.run(cmd, capture_output=True, text=True)


def render(
    *, values: str | tuple[str, ...] | None = None, sets: tuple[str, ...] = ()
) -> list[dict]:
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

    What the rule refuses and admits is proven through admission
    (test_policy_admission.py); what the CLI cannot show is here: a
    background scan has no requesting user, so a subject-matched rule cannot
    run as one, and a webhook that cannot be reached must refuse the write.
    And a policy that refuses a legal name is an outage, not a control, so
    the pattern mirrors the campaign-name rule (a DNS-1123 label).
    """
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    assert policy["spec"]["background"] is False
    assert policy["spec"]["failurePolicy"] == "Fail"
    web = rule(policy, "web-writes-status-only")
    assert _status_name_pattern(web) == (
        f"^campaign-{_DNS_LABEL_RE.pattern[:-2]}-status$"
    )


def _status_name_pattern(rule_body: dict) -> str:
    key = rule_body["validate"]["deny"]["conditions"]["all"][0]["key"]
    return re.search(r"regex_match\('([^']+)'", key).group(1)


@pytest.mark.parametrize(
    "campaign",
    ["kyrk", "sdhk-1500", "a", "kyrk-1600-1700", "sdhk-1500-b-2", "k" * 61],
)
def test_every_campaign_name_the_converter_accepts_may_have_a_status(
    full: list[dict], campaign: str
):
    """A policy that refused a name the converter renders would deny the
    read API's status write for that campaign, with nothing saying why. The
    first pattern here was narrower than the converter's rule; this one
    mirrors it (3087 then narrowed both to a DNS label: the API server
    refuses a dotted campaign's pods).
    """
    manifest = "https://example.org/manifest"
    Campaign(name=campaign, pipeline="p", volumes=[{"id": "R1", "manifest": manifest}])
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    pattern = _status_name_pattern(rule(policy, "web-writes-status-only"))
    assert re.match(pattern, f"campaign-{campaign}-status")


def test_the_rbac_scope_policy_follows_the_policies_switch(default: list[dict]):
    """Every ClusterPolicy this chart ships is behind
    `security.policies.enabled`, because a policy nothing reconciles is
    worse than none at all."""
    assert objects(default, "ClusterPolicy") == []


# --- B80: an install that enforces nothing has to say so ------------------


def test_an_install_without_the_policies_renders_once_it_says_so():
    """The chart's defaults leave every Kyverno policy off, because a policy
    nothing reconciles is worse than none -- which also meant an install
    that followed no profile enforced nothing the repository built, and
    nothing said so. Off stays possible; silent stops being."""
    # The refusal itself, in its own words, is the guard table's
    # `policies-off` case; what is left is that both ways out render.
    assert render(sets=DEFAULT_SETS)
    assert render(
        sets=REQUIRED_SETS + (PUBLIC_INGRESS, "security.policies.enabled=true")
    )


# --- D2: an unauthenticated NodePort open to every address by default -----


def test_the_catch_all_web_ingress_has_to_be_said_out_loud():
    """`network.web.ingressCidrs` defaults to every IPv4 address, in front of
    a NodePort with no authentication of its own. The default stays -- the
    dev stack and the compose smoke both rely on it, and narrowing it by
    guess would break them on upgrade -- but it stops being something an
    operator can ship without noticing."""
    refused = helm_template(sets=REQUIRED_SETS + (POLICIES_OFF,))
    assert refused.returncode != 0
    assert "network.web.ingressCidrs has 0.0.0.0/0, wider than /8" in refused.stderr
    assert "network.web.allowPublicIngress" in refused.stderr

    allowed = render(sets=DEFAULT_SETS)
    ingress = named(allowed, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "0.0.0.0/0"}}]


def test_a_named_ingress_range_needs_no_opt_out():
    """The flag is about the catch-all, not about ingress: an operator who
    lists the ranges that may reach the web front says enough by listing
    them."""
    rendered = render(
        sets=REQUIRED_SETS
        + (POLICIES_OFF, "network.web.ingressCidrs={198.51.100.0/24}")
    )
    ingress = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "198.51.100.0/24"}}]


@pytest.fixture
def empty_ingress(tmp_path: Path) -> Path:
    """`--set` cannot spell an empty list, so the operator's values file."""
    path = tmp_path / "empty-ingress.yaml"
    path.write_text("network:\n  web:\n    ingressCidrs: []\n", encoding="utf-8")
    return path


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
    ["{0.0.0.0/1,128.0.0.0/1}", "{198.51.100.0/24,0.0.0.0/7}", "{64.0.0.0/2}"],
)
def test_a_catch_all_split_into_halves_is_still_a_catch_all(cidrs: str):
    """The guard compared strings, so `0.0.0.0/1` + `128.0.0.0/1` -- every
    address, in two entries -- passed it (finding 3064). Anything wider than
    a /8 is somebody's whole internet and needs the same opt-in."""
    refused = helm_template(
        sets=REQUIRED_SETS + (POLICIES_OFF, f"network.web.ingressCidrs={cidrs}")
    )
    assert refused.returncode != 0
    assert "wider than /8" in refused.stderr
    assert "network.web.allowPublicIngress" in refused.stderr

    accepted = render(sets=DEFAULT_SETS + (f"network.web.ingressCidrs={cidrs}",))
    assert named(accepted, "NetworkPolicy", "htr-web")


def test_a_private_block_is_narrow_enough():
    """A /8 is the widest range an operator can name without the flag -- the
    `10.0.0.0/8` a site network commonly is."""
    rendered = render(
        sets=REQUIRED_SETS + (POLICIES_OFF, "network.web.ingressCidrs={10.0.0.0/8}")
    )
    ingress = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"]
    assert ingress[0]["from"] == [{"ipBlock": {"cidr": "10.0.0.0/8"}}]


def test_the_web_front_sees_its_clients_own_addresses(default: list[dict]):
    """With `externalTrafficPolicy: Cluster` a NodePort connection is SNAT'd
    to the node before the pod sees it, so the ingress list only ever
    matched node addresses -- and the docs told operators to list the node
    range, which every client reaching a node then matched (finding 3064).
    `Local` keeps the client's address, so the list restricts clients."""
    service = named(default, "Service", "htrflow-web")
    assert service["spec"]["type"] == "NodePort"  # the default, ingress mode off
    assert service["spec"]["externalTrafficPolicy"] == "Local"
    assert not objects(default, "Ingress")


def test_the_catch_all_guard_is_silent_when_the_policies_are_not_rendered():
    """A campaigns repo's CI renders this chart with `network.enabled=false`
    to get at the policy objects alone. There is no web NetworkPolicy in
    that render, so there is nothing for the guard to warn about."""
    result = helm_template(
        sets=REQUIRED_SETS + ("network.enabled=false", "security.policies.enabled=true")
    )
    assert result.returncode == 0, result.stderr


# --- T3: a ClusterIP + Ingress mode for the web front ----------------------

#: A deployment behind an ingress-nginx controller: a ClusterIP Service, an
#: Ingress with TLS, and a NetworkPolicy that admits the controller's pods
#: (by namespaceSelector) rather than client address ranges -- behind a
#: controller the pod only ever sees the controller's own address.
INGRESS = (
    "web.service.type=ClusterIP",
    "web.ingress.enabled=true",
    "web.ingress.className=nginx-test",
    "web.ingress.host=htr.example.org",
    "web.ingress.tlsSecretName=htr-tls",
    "network.web.ingressFrom[0].namespaceSelector.matchLabels.kubernetes\\.io/metadata\\.name=ingress-test",
)


def test_ingress_mode_renders_a_clusterip_service_and_a_tls_ingress():
    rendered = render(sets=REQUIRED_SETS + (POLICIES_OFF,) + INGRESS)
    svc = named(rendered, "Service", "htrflow-web")
    assert svc["spec"]["type"] == "ClusterIP"
    assert "nodePort" not in svc["spec"]["ports"][0]
    assert "externalTrafficPolicy" not in svc["spec"]
    ing = named(rendered, "Ingress", "htrflow-web")
    assert ing["spec"]["ingressClassName"] == "nginx-test"
    assert ing["spec"]["tls"] == [
        {"hosts": ["htr.example.org"], "secretName": "htr-tls"}
    ]
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "htr.example.org"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "htrflow-web", "port": {"number": 8081}}


def test_ingress_mode_admits_the_controller_not_address_ranges():
    rendered = render(sets=REQUIRED_SETS + (POLICIES_OFF,) + INGRESS)
    rule = named(rendered, "NetworkPolicy", "htr-web")["spec"]["ingress"][0]
    assert rule["from"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "ingress-test"}
            }
        }
    ]
    assert rule["ports"] == [{"port": 8081}]


def test_ingress_from_refuses_an_address_range():
    """network.web.ingressFrom is selectors on the controller's own pods,
    never an address range: the ingressCidrs guards are skipped whenever
    ingressFrom is non-empty (T3), so an ipBlock peer would sail straight
    past them -- reopening findings 3064/3100 on the unauthenticated web
    front (fix round 1). The schema refuses it before any template runs."""
    result = helm_template(
        sets=REQUIRED_SETS + ("network.web.ingressFrom[0].ipBlock.cidr=0.0.0.0/0",)
    )
    assert result.returncode != 0
    assert "additional properties 'ipBlock' not allowed" in result.stderr


def test_ingress_from_refuses_an_empty_peer(tmp_path: Path):
    """A NetworkPolicy peer naming neither selector matches nothing, not
    everything, but an operator who wrote it meant to name one -- and
    `--set` cannot spell `{}`, so the values file."""
    path = tmp_path / "empty-peer.yaml"
    path.write_text("network:\n  web:\n    ingressFrom: [{}]\n", encoding="utf-8")
    result = helm_template(values=str(path), sets=REQUIRED_SETS)
    assert result.returncode != 0
    assert "minProperties" in result.stderr


@pytest.mark.parametrize(
    "peer, at",
    [
        ("{namespaceSelector: {}}", "0/namespaceSelector"),
        ("{podSelector: {}}", "0/podSelector"),
        ("{namespaceSelector: {matchLabels: {}}}", "0/namespaceSelector/matchLabels"),
        ("{podSelector: {matchExpressions: []}}", "0/podSelector/matchExpressions"),
    ],
)
def test_ingress_from_refuses_a_selector_that_selects_everything(
    tmp_path: Path, peer: str, at: str
):
    """An empty *selector* is the opposite of an empty peer: `{}` (or an
    empty matchLabels / matchExpressions) selects every namespace or every
    pod, so `[{namespaceSelector: {}}]` admits every pod in the cluster to
    the unauthenticated front -- with the ingressCidrs guards skipped, since
    ingressFrom is set."""
    path = tmp_path / "empty-selector.yaml"
    path.write_text(f"network:\n  web:\n    ingressFrom: [{peer}]\n", encoding="utf-8")
    result = helm_template(values=str(path), sets=REQUIRED_SETS + (POLICIES_OFF,))
    assert result.returncode != 0
    assert f"at '/network/web/ingressFrom/{at}'" in result.stderr
    assert "got 0, want 1" in result.stderr


# --- D3: what a catch-all egress still reaches ----------------------------

#: k3s pod + service ranges (the chart's `clusterCidrs` default), loopback,
#: the link-local block every cloud serves instance credentials from, and
#: the three private ranges a VPC is built out of.
CATCH_ALL_EXCEPT = {
    "192.0.2.10/32",  # the API server REQUIRED_SETS names
    "10.42.0.0/16",
    "10.43.0.0/16",
    "169.254.0.0/16",
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
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


# --- 0923 D-3: every egress block wide enough to hold an internal range ----


def _blocks(policy: dict) -> dict[str, list[str]]:
    """Every egress ipBlock of a policy: cidr -> its `except` list."""
    return {
        to["ipBlock"]["cidr"]: to["ipBlock"].get("except", [])
        for rule in policy["spec"]["egress"]
        for to in rule.get("to", [])
        if "ipBlock" in to
    }


@pytest.mark.parametrize("policy_name", ["htr-batch-job", "htr-web"])
def test_a_catch_all_s3_range_is_carved_out_like_any_other(policy_name: str):
    """The carve-out applied to a literal `0.0.0.0/0` in iiifCidrs only, so
    `s3Cidrs: [0.0.0.0/0]` -- realistic for S3 on AWS, whose ranges move --
    let both pods that reach S3 open 169.254.169.254 and the cluster's own
    network on 443."""
    rendered = render(sets=DEFAULT_SETS + ("network.s3Cidrs={0.0.0.0/0}",))
    blocks = _blocks(named(rendered, "NetworkPolicy", policy_name))
    assert set(blocks["0.0.0.0/0"]) == CATCH_ALL_EXCEPT


def test_a_catch_all_split_in_halves_is_carved_out_half_by_half():
    """`0.0.0.0/1` + `128.0.0.0/1` is every address in two entries, and no
    entry was the literal catch-all, so neither was carved: the metadata
    address was open again. Each half now loses every internal range it
    holds -- and only those, since `except` must lie inside its block."""
    rendered = render(
        sets=DEFAULT_SETS + ("network.iiifCidrs={0.0.0.0/1,128.0.0.0/1}",)
    )
    blocks = _blocks(named(rendered, "NetworkPolicy", "htr-batch-job"))
    assert set(blocks["0.0.0.0/1"]) == {
        "10.42.0.0/16",
        "10.43.0.0/16",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
    }
    assert set(blocks["128.0.0.0/1"]) == {
        "192.0.2.10/32",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
    }


def test_the_apply_pods_git_range_is_carved_out_too():
    """The apply pod holds a token that may write Jobs; a catch-all git
    range must not hand it the metadata address either."""
    rendered = render(
        sets=DEFAULT_SETS + ("apply.rbac.enabled=true", "apply.gitCidrs={0.0.0.0/0}")
    )
    blocks = _blocks(named(rendered, "NetworkPolicy", "htr-campaigns-apply"))
    assert set(blocks["0.0.0.0/0"]) == CATCH_ALL_EXCEPT


def test_the_api_server_is_carved_out_whatever_its_address():
    """The API server was carved out only when it happened to be a node
    address or inside a private block; an endpoint on a public address was
    reachable from the warm-up and from any wide range (audit 0923 M-3)."""
    sets = DEFAULT_SETS
    rendered = render(
        sets=sets
        + (
            "network.apiServer.cidr=203.0.113.5/32",
            "network.apiServer.cidrs={198.51.100.7/32}",
            "network.iiifCidrs={0.0.0.0/0}",
        )
    )
    for name in ("htr-batch-job", "htr-warmup"):
        blocks = _blocks(named(rendered, "NetworkPolicy", name))
        assert {"203.0.113.5/32", "198.51.100.7/32"} <= set(blocks["0.0.0.0/0"]), name


def test_a_named_range_inside_a_private_block_is_left_whole():
    """A range inside an internal one is the operator naming a host on
    their own network: it holds no internal range, so it has nothing to
    carve out and stays reachable. A private block named whole still loses
    the pod and service ranges inside it."""
    rendered = render(sets=DEFAULT_SETS + ("network.s3Cidrs={10.9.5.5/32,10.0.0.0/8}",))
    blocks = _blocks(named(rendered, "NetworkPolicy", "htr-batch-job"))
    assert blocks["10.9.5.5/32"] == []
    assert set(blocks["10.0.0.0/8"]) == {"10.42.0.0/16", "10.43.0.0/16"}


@pytest.mark.parametrize("policy_name", ["htr-batch-job", "htr-web"])
def test_without_in_namespace_s3_no_pod_labelled_rustfs_is_a_route(policy_name: str):
    """The in-namespace `app: rustfs` rule is the dev stack's bucket. Off
    it, any pod that carries the label is a destination the batch Job and
    the web front may send to, so a production profile drops the rule."""
    rendered = render(
        sets=DEFAULT_SETS
        + ("network.s3InNamespace=false", "network.s3Cidrs={192.0.2.128/25}")
    )
    egress = named(rendered, "NetworkPolicy", policy_name)["spec"]["egress"]
    assert not any(
        "podSelector" in to and "namespaceSelector" not in to
        for r in egress
        for to in r.get("to", [])
    )
    assert any(
        {"ipBlock": {"cidr": "192.0.2.128/25"}} in r.get("to", []) for r in egress
    )


@pytest.mark.parametrize(
    "cidr",
    ["10.0.0.0/33", "10.0.0.0/99", "300.0.0.0/8", "10.256.0.0/16", "01.2.3.4/32"],
)
def test_the_schema_refuses_an_address_range_that_is_not_one(cidr: str):
    """A prefix past /32 or an octet past 255 is no IPv4 range; the carve-out
    helper does integer arithmetic on both, and the API server would refuse
    the NetworkPolicy only at install."""
    result = helm_template(sets=DEFAULT_SETS + (f"network.iiifCidrs={{{cidr}}}",))
    assert result.returncode != 0
    assert "/network/iiifCidrs/0" in result.stderr


def test_the_schema_takes_every_real_address_range():
    render(
        sets=DEFAULT_SETS
        + ("network.iiifCidrs={0.0.0.0/0,255.255.255.255/32,10.9.199.250/29}",)
    )


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


# --- D6: the apply identity's delete is namespace-wide --------------------


def test_the_apply_identity_may_only_delete_what_the_converter_rendered(
    full: list[dict],
):
    """`--prune` is a delete, so the Role grants one -- over every Job and
    ConfigMap in the namespace. The rule that holds it to converter-labelled
    objects must read `request.oldObject`: a DELETE admission review carries
    the object there, and `request.object` is null, so a rule reading it
    would compare against nothing and admit every delete.

    This is the only test of that choice. The Kyverno CLI cannot tell the
    two apart -- it fills both from the resource it is given -- so the
    admission test of the delete rule passes whichever one the rule reads.
    The rest of the rule is proven through admission there."""
    policy = named(full, "ClusterPolicy", f"htrflow-batch-rbac-scope-{NAMESPACE}")
    prune = rule(policy, "apply-deletes-only-what-it-rendered")
    condition = prune["validate"]["deny"]["conditions"]["all"][0]
    assert "request.oldObject.metadata.labels" in condition["key"]
    assert "request.object." not in condition["key"]


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
    assert api["to"] == [{"ipBlock": {"cidr": "192.0.2.56/32"}}]
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


APPLY_ON = "apply.rbac.enabled=true"


def _egress(policy: dict) -> list[dict]:
    return policy["spec"]["egress"]


def test_the_apply_identity_reaches_no_git_host_by_default():
    rendered = render(sets=DEFAULT_SETS + (APPLY_ON,))
    rules = _egress(named(rendered, "NetworkPolicy", "htr-campaigns-apply"))
    assert all({"port": 443} not in r.get("ports", []) for r in rules), (
        "no git egress unless apply.gitCidrs names the host"
    )


def test_the_apply_identity_reaches_the_listed_git_host_on_443():
    rendered = render(
        sets=DEFAULT_SETS + (APPLY_ON, "apply.gitCidrs={198.51.100.10/32}")
    )
    rules = _egress(named(rendered, "NetworkPolicy", "htr-campaigns-apply"))
    assert {
        "to": [{"ipBlock": {"cidr": "198.51.100.10/32"}}],
        "ports": [{"port": 443}],
    } in rules


def test_an_empty_git_port_list_is_refused_not_opened(tmp_path: Path):
    """A NetworkPolicy rule with `ports:` and nothing under it matches every
    port, so an empty apply.gitPorts would open the git host wide."""
    path = tmp_path / "no-git-ports.yaml"
    path.write_text("apply:\n  gitPorts: []\n", encoding="utf-8")
    result = helm_template(
        values=str(path),
        sets=DEFAULT_SETS + (APPLY_ON, "apply.gitCidrs={198.51.100.10/32}"),
    )
    assert result.returncode != 0
    assert "at '/apply/gitPorts'" in result.stderr
    assert "minItems: got 0, want 1" in result.stderr


def test_the_apply_identity_may_hold_its_run_lease_and_no_other():
    """`htrflow-campaigns apply` holds the coordination Lease
    `htrflow-campaigns-apply` for its whole run, so two applies never
    interleave, and fails closed without it. `create` cannot be scoped by
    name in RBAC; reading and renewing can, and are."""
    rendered = render(sets=DEFAULT_SETS + (APPLY_ON,))
    rules = named(rendered, "Role", "htrflow-campaigns")["rules"]
    leases = [r for r in rules if r["resources"] == ["leases"]]
    assert {
        "apiGroups": ["coordination.k8s.io"],
        "resources": ["leases"],
        "verbs": ["create"],
    } in leases
    assert {
        "apiGroups": ["coordination.k8s.io"],
        "resources": ["leases"],
        "resourceNames": ["htrflow-campaigns-apply"],
        "verbs": ["get", "update"],
    } in leases
    assert len(leases) == 2


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
            "network.apiServer.cidrs={192.0.2.11/32,192.0.2.12/32}",
        )
    )
    api = _api_rule(named(rendered, "NetworkPolicy", policy_name))
    assert api["to"] == [
        {"ipBlock": {"cidr": "192.0.2.10/32"}},
        {"ipBlock": {"cidr": "192.0.2.11/32"}},
        {"ipBlock": {"cidr": "192.0.2.12/32"}},
    ]
    assert api["ports"] == [{"port": 6443}]


def test_the_list_alone_is_enough():
    sets = ("network.apiServer.cidr=",)
    rendered = render(
        sets=sets
        + (PUBLIC_INGRESS, POLICIES_OFF, "network.apiServer.cidrs={192.0.2.11/32}")
    )
    api = _api_rule(named(rendered, "NetworkPolicy", "htr-web"))
    assert api["to"] == [{"ipBlock": {"cidr": "192.0.2.11/32"}}]


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
                "addresses": [{"ip": "192.0.2.11"}, {"ip": "192.0.2.12"}],
                "ports": [{"name": "https", "port": 6443, "protocol": "TCP"}],
            },
            {
                "addresses": [{"ip": "192.0.2.13"}, {"ip": "192.0.2.11"}],
                "ports": [{"name": "https", "port": 6443, "protocol": "TCP"}],
            },
        ]
    }
    assert _from_endpoints(tmp_path, endpoints) == {
        "cidrs": ["192.0.2.11/32", "192.0.2.12/32", "192.0.2.13/32"],
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

#: The repository the release is published from (SECURITY.md's reporting
#: link names it): the one part of the signing identity no workflow file says.
REPOSITORY = "AI-Riksarkivet/htrflow-batch"


def _signing_subject() -> str:
    """The identity Sigstore certifies for a release signature, derived from
    the workflow that signs: the file whose jobs run the sign-attest action,
    triggered only by `workflow_dispatch` -- so its certificate names the
    branch it ran from, which the release process runs on main, never a
    tag."""
    signing = [
        w
        for w in sorted((REPO / ".github" / "workflows").glob("*.yml"))
        if "./.github/actions/sign-attest" in w.read_text(encoding="utf-8")
    ]
    assert len(signing) == 1, signing
    triggers = yaml.safe_load(signing[0].read_text(encoding="utf-8"))[True]
    assert set(triggers) == {"workflow_dispatch"}, triggers
    path = signing[0].relative_to(REPO).as_posix()
    return f"https://github.com/{REPOSITORY}/{path}@refs/heads/main"


SIGNING_SUBJECT = _signing_subject()


def test_the_signing_identity_the_profile_verifies_is_the_one_the_release_signs_as():
    """Both copies of the cosign subject once named the wrong organisation,
    and a fixture asked for `@refs/tags/*`. An operator who copies either
    gets a policy that refuses every image the release publishes -- and
    finds out at admission, on a cluster (test audit TA-infra-8)."""
    prod = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
    assert prod["security"]["verifyImages"]["subject"] == SIGNING_SUBJECT
    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    assert f"e.g. {SIGNING_SUBJECT}" in values


def test_verification_reads_the_sigstore_bundles_the_release_writes(
    prod: list[dict],
):
    """The release signs with cosign 3, which stores each signature as a
    Sigstore bundle attached to the image as an OCI referrer and writes no
    `sha256-<digest>.sig` tag. Kyverno's default attestor type looks only
    for that tag, so under the production profile it found no signature on
    any published image and refused every pod in the namespace (audit 0923
    D-1). `dagger call verify-published` checks the same rule against the
    real published digests; this pins the field it depends on."""
    policy = named(prod, "ClusterPolicy", f"htrflow-batch-verify-images-{NAMESPACE}")
    for entry in policy["spec"]["rules"][0]["verifyImages"]:
        assert entry["type"] == "SigstoreBundle"


# --- the renders every input produces (moved from dagger's text checks) ----


@pytest.mark.parametrize(
    "fixture", ["default", "full", "prod"], ids=["default", "full", "prod"]
)
def test_every_render_is_the_platform_and_nothing_else(fixture: str, request):
    """The campaign controller CronJob is gone (B63), the web front always
    renders with its health probe, and nothing of the dev stack leaks into
    this chart. Asserted on the parsed objects: a text match on
    `livenessProbe` was satisfied by a comment (test audit TA-infra-14)."""
    rendered = request.getfixturevalue(fixture)
    assert objects(rendered, "CronJob") == []
    assert not [
        o
        for o in rendered
        if (o["metadata"].get("labels") or {}).get("app.kubernetes.io/component")
        == "devstack"
    ]
    web = named(rendered, "Deployment", "htrflow-web")
    probe = web["spec"]["template"]["spec"]["containers"][0]["livenessProbe"]
    assert probe["httpGet"]["path"] == "/healthz"


# --- D14: defaults called production-shaped that enforce nothing ----------

#: What `values-prod.yaml` cannot know -- the bucket's public base, the API
#: server, the S3, cluster and IIIF ranges, who may reach the web front --
#: completed the way an operator's `--set` lines complete it: from
#: REQUIRED_VALUES and ci/prod-values.yaml, the copies `make helm-template`
#: and `dagger call check-chart` render with too.
PROD_VALUES = ("values-prod.yaml", "ci/prod-values.yaml")


@pytest.fixture(scope="module")
def prod() -> list[dict]:
    return render(values=PROD_VALUES)


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
            "job-shape",
        )
    }
    for policy in objects(prod, "ClusterPolicy"):
        assert policy["spec"]["validationFailureAction"] == "Enforce"

    values = yaml.safe_load((CHART / "values-prod.yaml").read_text(encoding="utf-8"))
    assert values["security"]["psaEnforce"] == "restricted"
    assert values["security"]["requireModelRevision"] is True
    assert values["security"]["allowedImageRepos"] == [
        "docker.io/riksarkivet/htrflow-batch",
        "docker.io/riksarkivet/htrflow-web",
        "docker.io/riksarkivet/htrflow-campaigns",
    ]


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


def _without(tmp_path: Path, name: str, dotted: str) -> str:
    """A copy of the chart's ci/<name> with one key left out."""
    values = yaml.safe_load((CHART / "ci" / name).read_text(encoding="utf-8"))
    *parents, leaf = dotted.split(".")
    holder = values
    for key in parents:
        holder = holder[key]
    del holder[leaf]
    path = tmp_path / name
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "file,left_out,reason",
    [
        ("default-values.yaml", "publicResultsBase", RESULTS_BASE_REFUSAL),
        ("default-values.yaml", "network.apiServer.cidr", API_SERVER_REFUSAL),
        (
            "prod-values.yaml",
            "network.web.ingressCidrs",
            "network.web.ingressCidrs has 0.0.0.0/0",
        ),
        # 0923 D-8: the profile's comment said these three were asked for,
        # and the render went through without them -- a production batch
        # Job with no route to S3 fails every volume after its GPU time.
        ("prod-values.yaml", "network.s3Cidrs", S3_NOWHERE_REFUSAL),
        ("prod-values.yaml", "network.clusterCidrs", CLUSTER_CIDRS_REFUSAL),
        ("prod-values.yaml", "network.iiifCidrs", IIIF_NOWHERE_REFUSAL),
    ],
)
def test_the_profile_leaves_the_site_specific_values_to_the_site(
    tmp_path: Path, file: str, left_out: str, reason: str
):
    """A profile that guessed the results base, the API server address, the
    network ranges or who may reach the web front would be wrong on every
    cluster. It must fail asking for each of them, not render something
    plausible -- one at a time, so the profile guessing any one of them is
    caught, not just all of them."""
    stripped = _without(tmp_path, file, left_out)
    if file == "default-values.yaml":
        refused = helm_template(required=stripped, values=PROD_VALUES)
    else:
        refused = helm_template(values=(PROD_VALUES[0], stripped))
    assert refused.returncode != 0
    assert reason in refused.stderr


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


# --- 0923 D-9: cluster-scoped Kueue objects are created or referenced ------


def test_an_existing_flavor_is_referenced_not_recreated():
    """A cluster whose Kueue already has `default-flavor` refused the first
    install: Helm will not adopt an object another owner made."""
    rendered = render(sets=DEFAULT_SETS + ("queue.createFlavor=false",))
    assert objects(rendered, "ResourceFlavor") == []
    queue = named(rendered, "ClusterQueue", "htr-batch-cq")
    flavors = queue["spec"]["resourceGroups"][0]["flavors"]
    assert [f["name"] for f in flavors] == ["default-flavor"]


def test_an_existing_cluster_queue_is_referenced_by_name():
    rendered = render(
        sets=DEFAULT_SETS
        + ("queue.createClusterQueue=false", "queue.clusterQueueName=shared-cq")
    )
    assert objects(rendered, "ClusterQueue") == []
    local = named(rendered, "LocalQueue", "htr-batch")
    assert local["spec"]["clusterQueue"] == "shared-cq"


def test_a_second_release_can_name_its_own_cluster_objects():
    rendered = render(
        sets=DEFAULT_SETS
        + (
            "queue.clusterQueueName=team-b-cq",
            "queue.flavor=team-b-flavor",
            "queue.createPriorityClasses=false",
        )
    )
    named(rendered, "ResourceFlavor", "team-b-flavor")
    named(rendered, "ClusterQueue", "team-b-cq")
    assert (
        named(rendered, "LocalQueue", "htr-batch")["spec"]["clusterQueue"]
        == "team-b-cq"
    )
    assert objects(rendered, "WorkloadPriorityClass") == []


# --- B104: one flavor or several -------------------------------------------

#: The queue objects an install that does not set `queue.flavors` renders,
#: recorded before the list existed: the single-flavor case has to stay
#: exactly what it was, or an upgrade would change a live ClusterQueue.
#: One case per D-9 toggle, since each takes its own branch of kueue.yaml.
QUEUE_GOLDEN = Path(__file__).parent / "golden" / "chart-queue.yaml"
QUEUE_CASES = {
    "default": (),
    "flavor-referenced": ("queue.createFlavor=false",),
    "queue-referenced": (
        "queue.createClusterQueue=false",
        "queue.clusterQueueName=shared-cq",
    ),
    "renamed": (
        "queue.flavor=team-b-flavor",
        "queue.clusterQueueName=team-b-cq",
        'json:queue.resources=[{"name":"cpu","quota":8},{"name":"memory","quota":"16Gi"},'
        '{"name":"nvidia.com/gpu","quota":2}]',
        "queue.createPriorityClasses=false",
    ),
}


def _queue_objects(sets: tuple[str, ...]) -> list[dict]:
    """The Kueue objects of a render, without the chart-version label (a
    release moves it, and it is not what an install's queue is)."""
    found = [o for o in render(sets=DEFAULT_SETS + sets) if "kueue" in o["apiVersion"]]
    for o in found:
        o["metadata"]["labels"].pop("helm.sh/chart")
    return found


@pytest.mark.parametrize("case", QUEUE_CASES)
def test_an_install_without_flavors_renders_the_queue_it_always_did(case: str):
    golden = yaml.safe_load(QUEUE_GOLDEN.read_text(encoding="utf-8"))
    assert _queue_objects(QUEUE_CASES[case]) == golden[case]


#: Two sorts of GPU, as ci/full-values.yaml describes them: the render
#: kubeconform checks against Kueue's own CRD schemas in `make helm-template`.
TWO_FLAVORS = [
    {
        "name": "large-gpu",
        "nodeLabels": {"nvidia.com/gpu.product": "NVIDIA-A100-SXM4-80GB"},
        "nodeTaints": [{"key": "gpu-pool", "value": "large", "effect": "NoSchedule"}],
        "quota": {"cpu": 16, "memory": "128Gi", "nvidia.com/gpu": 2},
    },
    {
        "name": "small-gpu",
        "nodeLabels": {"nvidia.com/gpu.product": "NVIDIA-L4"},
        "quota": {"cpu": 8, "memory": "32Gi", "nvidia.com/gpu": 4},
    },
]


def test_two_flavors_render_two_resource_flavors_with_their_node_labels(
    full: list[dict],
):
    flavors = {f["metadata"]["name"]: f for f in objects(full, "ResourceFlavor")}
    assert list(flavors) == ["large-gpu", "small-gpu"]
    for want in TWO_FLAVORS:
        spec = flavors[want["name"]]["spec"]
        assert spec["nodeLabels"] == want["nodeLabels"]
    # A taint the flavor's nodes carry is tolerated by what Kueue admits on
    # the flavor: Kueue adds the flavor's tolerations to the pod at
    # admission, and counts them when it checks the taints (v1beta2
    # ResourceFlavorSpec), so the converter need not know the pool's taint.
    large = flavors["large-gpu"]["spec"]
    assert large["nodeTaints"] == TWO_FLAVORS[0]["nodeTaints"]
    assert large["tolerations"] == [
        {
            "key": "gpu-pool",
            "operator": "Equal",
            "value": "large",
            "effect": "NoSchedule",
        }
    ]
    assert "nodeTaints" not in flavors["small-gpu"]["spec"]
    assert "tolerations" not in flavors["small-gpu"]["spec"]


def test_the_cluster_queue_has_a_quota_per_flavor_in_the_order_values_list_them(
    full: list[dict],
):
    """Kueue tries a resource group's flavors in the order the ClusterQueue
    lists them, so the order is the operator's to choose."""
    queue = named(full, "ClusterQueue", "htr-batch-cq")
    (group,) = queue["spec"]["resourceGroups"]
    assert group["coveredResources"] == ["cpu", "memory", "nvidia.com/gpu"]
    assert group["flavors"] == [
        {
            "name": f["name"],
            "resources": [
                {"name": r, "nominalQuota": f["quota"][r]}
                for r in ("cpu", "memory", "nvidia.com/gpu")
            ],
        }
        for f in TWO_FLAVORS
    ]


def test_flavors_that_exist_already_are_referenced_not_created():
    rendered = render(values="ci/full-values.yaml", sets=("queue.createFlavor=false",))
    assert objects(rendered, "ResourceFlavor") == []
    queue = named(rendered, "ClusterQueue", "htr-batch-cq")
    names = [f["name"] for f in queue["spec"]["resourceGroups"][0]["flavors"]]
    assert names == ["large-gpu", "small-gpu"]


def test_the_list_replaces_the_single_flavor(full: list[dict]):
    """With `queue.flavors` set, `queue.flavor` and `queue.resources` are
    not a third flavor beside it."""
    names = [f["metadata"]["name"] for f in objects(full, "ResourceFlavor")]
    assert "default-flavor" not in names


@pytest.mark.parametrize(
    "flavor",
    [
        {"name": "x", "nodeLabels": {"a": "b"}, "quota": {"cpu": 1, "memory": "1Gi"}},
        {"name": "x", "quota": {"cpu": 1, "memory": "1Gi", "nvidia.com/gpu": 1}},
        {
            "name": "x",
            "nodeLabels": {"a": "b"},
            "quota": {"cpu": 1, "memory": "1Gi", "nvidia.com/gpu": 1, "pods": 5},
        },
        {
            "name": "x",
            "nodeLabels": {"a": "b"},
            "quota": {"cpu": 1, "memory": "1Gi", "nvidia.com/gpu": 0},
        },
        {
            "name": "x",
            "nodeLabels": {"a": "b"},
            "nodeTaints": [{"key": "k", "effect": "Sometimes"}],
            "quota": {"cpu": 1, "memory": "1Gi", "nvidia.com/gpu": 1},
        },
    ],
    ids=["no-gpu-quota", "no-node-labels", "unknown-resource", "zero-gpus", "effect"],
)
def test_the_schema_refuses_a_flavor_kueue_could_not_admit_a_pod_on(
    flavor: dict, tmp_path: Path
):
    """Every flavor quotes the three resources a campaign pod requests: a
    resource the group does not cover is a Workload Kueue never admits."""
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump({"queue": {"flavors": [flavor]}}), encoding="utf-8")
    refused = helm_template(values=str(path), sets=DEFAULT_SETS)
    assert refused.returncode != 0
    assert "queue/flavors/0" in refused.stderr, refused.stderr


# --- 3103: every guard, alone, refuses in its own words -------------------

#: One case per `fail`/`required` in the two charts: a render that satisfies
#: every other guard and breaks exactly this one, and the whole sentence the
#: chart must refuse it with. A test that looked only at the exit code was
#: satisfied by whichever guard happened to fire, so deleting any single one
#: went unnoticed (finding 3103). `values` is a values file's text, for what
#: `--set` cannot spell.
BATCH_GUARDS = {
    "policies-off": (
        None,
        REQUIRED_SETS + (PUBLIC_INGRESS,),
        "security.policies.enabled is false, so nothing in this namespace"
        " refuses an image from any registry, a tag instead of a digest or an"
        " unpinned model: install Kyverno and set security.policies.enabled=true"
        " (values-prod.yaml does), or set security.policies.allowDisabled=true"
        " to accept that",
    ),
    "verify-images-no-identity": (
        None,
        DEFAULT_SETS + ("security.verifyImages.enabled=true",),
        "security.verifyImages.issuer and .subject are required when"
        " security.verifyImages.enabled",
    ),
    "verify-images-no-subject": (
        None,
        DEFAULT_SETS
        + (
            "security.verifyImages.enabled=true",
            "security.verifyImages.issuer=https://token.actions.githubusercontent.com",
        ),
        "security.verifyImages.issuer and .subject are required when"
        " security.verifyImages.enabled",
    ),
    "verify-images-no-issuer": (
        None,
        DEFAULT_SETS
        + (
            "security.verifyImages.enabled=true",
            f"security.verifyImages.subject={SIGNING_SUBJECT}",
        ),
        "security.verifyImages.issuer and .subject are required when"
        " security.verifyImages.enabled",
    ),
    "ingress-empty": (
        "network:\n  web:\n    ingressCidrs: []\n",
        REQUIRED_SETS + (POLICIES_OFF,),
        "network.web.ingressCidrs is empty, and a NetworkPolicy rule with no"
        " sources admits every address, so an empty list would open the"
        " unauthenticated web front to everyone rather than close it: list the"
        " ranges that may reach it, or set network.web.allowPublicIngress=true"
        " to accept that any address may",
    ),
    "ingress-wider-than-8": (
        None,
        REQUIRED_SETS
        + (POLICIES_OFF, "network.web.ingressCidrs={198.51.100.0/24,8.0.0.0/7}"),
        "network.web.ingressCidrs has 8.0.0.0/7, wider than /8, and the web"
        " front has no authentication of its own: list the ranges your clients'"
        " addresses are in, or set network.web.allowPublicIngress=true to accept"
        " that any address that can route to a node may open the campaign"
        " browser, the viewer and the read API",
    ),
    "ingress-not-clusterip": (
        None,
        DEFAULT_SETS
        + (
            "web.ingress.enabled=true",
            "web.ingress.host=htr.example.org",
            "network.web.ingressFrom[0].namespaceSelector.matchLabels"
            ".kubernetes\\.io/metadata\\.name=ingress-test",
        ),
        "web.ingress.enabled needs web.service.type=ClusterIP",
    ),
    "ingress-no-host": (
        None,
        DEFAULT_SETS
        + (
            "web.service.type=ClusterIP",
            "web.ingress.enabled=true",
            "network.web.ingressFrom[0].namespaceSelector.matchLabels"
            ".kubernetes\\.io/metadata\\.name=ingress-test",
        ),
        "web.ingress.enabled needs web.ingress.host",
    ),
    "ingress-no-ingress-from": (
        None,
        DEFAULT_SETS
        + (
            "web.service.type=ClusterIP",
            "web.ingress.enabled=true",
            "web.ingress.host=htr.example.org",
        ),
        "web.ingress.enabled needs network.web.ingressFrom",
    ),
    "api-server": (
        None,
        DEFAULT_SETS + ("network.apiServer.cidr=",),
        API_SERVER_REFUSAL,
    ),
    "web-image-tag": (
        None,
        DEFAULT_SETS + ("web.image=docker.io/riksarkivet/htrflow-web:v1",),
        "web.image must be pinned by digest (…@sha256:<64 hex>), got"
        ' "docker.io/riksarkivet/htrflow-web:v1"; set security.allowTagImages=true'
        " only for a PoC iteration loop",
    ),
    "results-base": (
        None,
        DEFAULT_SETS + ("publicResultsBase=",),
        RESULTS_BASE_REFUSAL,
    ),
    "s3-nowhere": (
        None,
        DEFAULT_SETS + ("network.s3InNamespace=false",),
        S3_NOWHERE_REFUSAL,
    ),
    "cluster-cidrs-empty": (
        "network:\n  clusterCidrs: []\n",
        DEFAULT_SETS,
        CLUSTER_CIDRS_REFUSAL,
    ),
    "iiif-empty": (
        "network:\n  iiifCidrs: []\n",
        DEFAULT_SETS,
        IIIF_NOWHERE_REFUSAL,
    ),
    "flavor-twice": (
        yaml.safe_dump({"queue": {"flavors": [TWO_FLAVORS[1], TWO_FLAVORS[1]]}}),
        DEFAULT_SETS,
        "queue.flavors names small-gpu twice: a ClusterQueue lists a flavor"
        " once, so rename one of them",
    ),
    "flavor-zero-quota": (
        yaml.safe_dump(
            {
                "queue": {
                    "flavors": [
                        {
                            **TWO_FLAVORS[1],
                            "quota": {**TWO_FLAVORS[1]["quota"], "memory": "0Gi"},
                        }
                    ]
                }
            }
        ),
        DEFAULT_SETS,
        'queue.flavors small-gpu has a memory quota of "0Gi": a flavor with'
        " none of a resource every campaign pod requests admits no campaign"
        " pod, so give it more than 0 or leave the flavor out",
    ),
}
DEVSTACK_GUARDS = {
    "console-without-rustfs": (
        None,
        ("rustfs.console.enabled=true",),
        "rustfs.console.enabled needs rustfs.enabled",
    ),
    "rustfs-generated-credentials": (
        None,
        ("rustfs.enabled=true",),
        "rustfs.accessKey/secretKey is empty or a value published in this repo:"
        " set credentials of your own, or set devStack.insecureDefaults: true to"
        " accept generated or known ones (charts/htrflow-devstack/values.yaml"
        " says why)",
    ),
    "rustfs-published-credentials": (
        None,
        (
            "rustfs.enabled=true",
            "rustfs.accessKey=site-key",
            "rustfs.secretKey=minioadmin",
        ),
        "rustfs.accessKey/secretKey is empty or a value published in this repo:"
        " set credentials of your own, or set devStack.insecureDefaults: true to"
        " accept generated or known ones (charts/htrflow-devstack/values.yaml"
        " says why)",
    ),
}
#: Each chart's guards, and a render of it that none of them refuses.
GUARDED_CHARTS = {
    CHART: (BATCH_GUARDS, DEFAULT_SETS),
    DEVSTACK_CHART: (DEVSTACK_GUARDS, ()),
}


def _guard_render(
    chart: Path, values: str | None, sets: tuple[str, ...], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    path = None
    if values is not None:
        path = tmp_path / "values.yaml"
        path.write_text(values, encoding="utf-8")
    return helm_template(values=str(path) if path else None, sets=sets, chart=chart)


@pytest.mark.parametrize(
    "chart,case",
    [(chart, case) for chart, (guards, _) in GUARDED_CHARTS.items() for case in guards],
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_each_guard_refuses_in_its_own_words(chart: Path, case: str, tmp_path: Path):
    values, sets, reason = GUARDED_CHARTS[chart][0][case]
    refused = _guard_render(chart, values, sets, tmp_path)
    assert refused.returncode != 0
    assert reason in refused.stderr, refused.stderr


@pytest.mark.parametrize("chart", list(GUARDED_CHARTS), ids=lambda c: c.name)
def test_the_render_every_case_breaks_is_one_no_guard_refuses(chart: Path):
    """Each case is this render with one thing broken; if this one were
    refused, a case could pass on some other guard's sentence."""
    result = helm_template(sets=GUARDED_CHARTS[chart][1], chart=chart)
    assert result.returncode == 0, result.stderr


#: `fail "…"`, `fail (printf "…" …)` and `required "…"`: the sentence's
#: literal text, with `%s`/`%q` holes.
_GUARD_RE = re.compile(r'\b(?:fail|required)\s+\(?(?:printf\s+)?"((?:[^"\\]|\\.)*)"')


@pytest.mark.parametrize("chart", list(GUARDED_CHARTS), ids=lambda c: c.name)
def test_every_guard_in_the_templates_has_a_case(chart: Path):
    """The table above is only as good as its coverage: a guard added to a
    template without a case here fails this test, not a review."""
    sentences = [
        m.group(1)
        for tpl in sorted((chart / "templates").rglob("*"))
        if tpl.suffix in {".tpl", ".yaml"}
        for m in _GUARD_RE.finditer(tpl.read_text(encoding="utf-8"))
    ]
    assert sentences
    reasons = [reason for _, _, reason in GUARDED_CHARTS[chart][0].values()]
    # The network guard below is not in the table: see its own test.
    if chart == CHART:
        reasons.append(NETWORK_MISSING_REFUSAL)
    for sentence in sentences:
        pieces = [p for p in re.split(r"%[sqd]", sentence) if p]
        assert any(all(p in r for p in pieces) for r in reasons), sentence


NETWORK_MISSING_REFUSAL = (
    "`.Values.network` is missing: upgrade with --reset-then-reuse-values (or a"
    " full values file), never plain --reuse-values"
)


def test_a_values_tree_without_network_is_refused(tmp_path: Path):
    """`helm upgrade --reuse-values` with a chart that added `network` once
    rendered every NetworkPolicy away (audit O6). The schema refuses that
    tree before any template runs; `htrflow-batch.validate` repeats it for a
    render that skips the schema. With the schema skipped, though, web.yaml
    dereferences `.Values.network` before validate.yaml is reached (helm
    renders templates in reverse name order), so on the real chart that
    render dies on a nil pointer -- still refused, never in these words. The
    helper's own sentence is checked on it alone, the way the Endpoints
    reader is."""
    refused = helm_template(sets=DEFAULT_SETS + ("network=null",))
    assert refused.returncode != 0
    assert "missing property 'network'" in refused.stderr

    chart = tmp_path / "probe"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: probe\nversion: 0.0.0\n", encoding="utf-8"
    )
    shutil.copy(CHART / "templates" / "_helpers.tpl", chart / "templates")
    (chart / "templates" / "probe.yaml").write_text(
        '{{- include "htrflow-batch.validate" . }}\n', encoding="utf-8"
    )
    probe = subprocess.run(
        ["helm", "template", "probe", str(chart)], capture_output=True, text=True
    )
    assert probe.returncode != 0
    assert NETWORK_MISSING_REFUSAL in probe.stderr, probe.stderr


def test_a_tag_is_taken_only_with_the_poc_switch():
    """The digest guard's way out is the one its sentence names."""
    assert render(
        sets=DEFAULT_SETS
        + (
            "web.image=docker.io/riksarkivet/htrflow-web:v1",
            "security.allowTagImages=true",
        )
    )


def test_a_debug_container_is_checked_by_every_image_policy(prod: list[dict]):
    """`kubectl debug` adds its container through the `pods/ephemeralcontainers`
    subresource. A rule on kind Pod does not see that request, and one that
    does is skipped by Kyverno's default `allowExistingViolations` -- the
    request is an update to a Pod that, as Kyverno reads it, already carries
    the violation. On the dev cluster a busybox debug container got past
    both. The Kyverno CLI sends no subresource request, so the shape is held
    here and was proven against the cluster's admission controller."""
    policies = {
        p["metadata"]["name"]: p for p in prod if p.get("kind") == "ClusterPolicy"
    }
    for name, policy in policies.items():
        short = name.removeprefix("htrflow-batch-").rsplit("-", 1)[0]
        if short not in ("images-allowed", "images-pinned", "verify-images"):
            continue
        rules = [
            r
            for r in policy["spec"]["rules"]
            if any(
                "Pod/ephemeralcontainers" in res["resources"]["kinds"]
                for res in r["match"]["any"]
            )
        ]
        assert rules, (name, "no rule sees the debug subresource")
        for r in rules:
            if "validate" in r:
                assert r["validate"].get("allowExistingViolations") is False, (
                    name,
                    r["name"],
                )
                assert "ephemeralContainers" in str(r["context"]), (name, r["name"])


def test_a_policy_that_matches_a_subresource_is_not_a_background_policy(
    prod: list[dict],
):
    """Kyverno's own webhook refuses a ClusterPolicy that matches a
    subresource kind (`Pod/ephemeralcontainers`) with `background: true` --
    the chart would not install. The Kyverno CLI the admission tests run
    does not make that check, so it is made here."""
    for policy in prod:
        if policy.get("kind") != "ClusterPolicy":
            continue
        kinds = [
            k
            for r in policy["spec"]["rules"]
            for res in r["match"].get("any", [])
            for k in res.get("resources", {}).get("kinds", [])
        ]
        if any("/" in k for k in kinds):
            assert policy["spec"].get("background") is False, policy["metadata"]["name"]
