"""The Argo CD PostSync hook `init` writes (template/argocd/apply.yaml)."""

import re
import tomllib
from importlib import resources
from pathlib import Path

import yaml

HOOK = resources.files("htrflow_converter") / "template" / "argocd" / "apply.yaml"
REPO = Path(__file__).resolve().parents[3]
WRAPPER_PYPROJECT = REPO / "packages" / "wrapper" / "pyproject.toml"
RELEASING = REPO / "docs" / "development" / "releasing.md"
IMAGE = re.compile(
    r"^docker\.io/riksarkivet/htrflow-campaigns(@sha256:[0-9a-f]{64}|:v\d+\.\d+\.\d+)$"
)


def job() -> dict:
    return yaml.safe_load(HOOK.read_text())


def test_it_is_a_postsync_hook_argo_replaces_each_sync():
    ann = job()["metadata"]["annotations"]
    assert ann["argocd.argoproj.io/hook"] == "PostSync"
    assert (
        ann["argocd.argoproj.io/hook-delete-policy"]
        == "BeforeHookCreation,HookSucceeded"
    )


def test_it_runs_as_the_apply_identity_under_its_egress_rule():
    pod = job()["spec"]["template"]
    assert pod["metadata"]["labels"]["app"] == "htrflow-campaigns"
    assert pod["spec"]["serviceAccountName"] == "htrflow-campaigns"


def test_every_container_is_restricted_and_uses_the_converter_image():
    spec = job()["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for c in spec["initContainers"] + spec["containers"]:
        assert IMAGE.match(c["image"]), c["image"]
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["readOnlyRootFilesystem"] is True
        assert sc["capabilities"] == {"drop": ["ALL"]}


def test_the_token_comes_from_the_secret_never_the_command_line():
    spec = job()["spec"]["template"]["spec"]
    clone = spec["initContainers"][0]
    env = {e["name"]: e for e in clone["env"]}
    assert env["GIT_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "htrflow-campaigns-git",
        "key": "token",
    }
    # The script reads the token from its environment at run time; no token
    # value is ever written into the manifest or the command line.
    assert 'os.environ["GIT_TOKEN"]' in clone["args"][0]
    assert "value" not in env["GIT_TOKEN"]


def test_the_apply_prunes_the_checkout_the_clone_wrote():
    spec = job()["spec"]["template"]["spec"]
    apply = spec["containers"][0]
    assert apply["args"] == ["apply", "--prune", "/repo"]
    mounts = {m["name"]: m["mountPath"] for m in apply["volumeMounts"]}
    assert mounts["repo"] == "/repo"


def test_the_clone_names_a_user_for_its_reflog():
    """dulwich writes a reflog entry for every ref a clone creates, and asks
    for the local user's identity to do it: $LOGNAME/$USER/$LNAME/$USERNAME,
    else the passwd entry of the uid. The pod runs as uid 1000, which the
    distroless image has no passwd entry for, so without one of those set
    the clone fetches everything and then dies on DefaultIdentityNotFound
    (seen on a live cluster)."""
    clone = job()["spec"]["template"]["spec"]["initContainers"][0]
    env = {e["name"]: e.get("value") for e in clone["env"]}
    assert env.get("USER"), clone["env"]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_a_tagged_hook_image_is_never_an_older_release():
    """Until a release commit pins its digest, the hook names the converter
    image by the tag of the release it will ship in. The publish workflow
    tags every image `v` + the wrapper's version, so once that version
    moves past the tag the hook is a release behind: the release that bumped
    it forgot to pin, and this fails CI instead of shipping the old image."""
    release = _version(
        tomllib.loads(WRAPPER_PYPROJECT.read_text(encoding="utf-8"))["project"][
            "version"
        ]
    )
    spec = job()["spec"]["template"]["spec"]
    for c in spec["initContainers"] + spec["containers"]:
        _, sep, tag = c["image"].partition(":v")
        if sep and "@" not in c["image"]:
            assert _version(tag) >= release, (
                f"{c['name']} runs {c['image']}, older than the wrapper's "
                f"{'.'.join(map(str, release))}: pin the converter digest the "
                "release published (docs/development/releasing.md)"
            )


def test_the_release_procedure_names_every_pin_the_hook_and_ci_need():
    """The release commit pins three manifest-list digests; the converter's
    goes into this hook and its tag into the CI templates' CONVERTER_REF.
    A procedure that forgot either would leave them on the previous
    release."""
    doc = RELEASING.read_text(encoding="utf-8")
    assert "template/argocd/apply.yaml" in doc
    assert "CONVERTER_REF" in doc
    assert "ci/github/.github/workflows/render.yml" in doc
    assert "ci/azure/azure-pipelines.yml" in doc
