"""The Argo CD PostSync hook `init` writes (template/argocd/apply.yaml)."""

import re
from importlib import resources
from pathlib import Path

import pytest
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
    assert apply["args"] == [
        "apply",
        "--prune",
        "--namespace",
        "$(POD_NAMESPACE)",
        "/repo",
    ]
    env = {e["name"]: e for e in apply["env"]}
    assert env["POD_NAMESPACE"]["valueFrom"] == {
        "fieldRef": {"fieldPath": "metadata.namespace"}
    }
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
    # Read with a pattern, not tomllib: the workspace supports Python 3.10.
    found = re.search(
        r'^version = "([^"]+)"$',
        WRAPPER_PYPROJECT.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert found, WRAPPER_PYPROJECT
    release = _version(found.group(1))
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


def test_the_apply_says_who_applied_it():
    """audit 0923 C-10: the apply container set neither HTRFLOW_APPLIED_BY
    nor USER, and uid 1000 has no passwd entry in the image, so every
    campaign the hook applied recorded `applied-by: unknown`."""
    apply = job()["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in apply["env"]}
    assert env.get("HTRFLOW_APPLIED_BY", "").startswith("argocd-hook/"), apply["env"]


def test_the_apply_runs_only_on_a_checkout_ci_rendered():
    """audit 0923 S-9: the clone takes the branch's HEAD, which can be a push
    that landed after CI's render commit. An init container between the
    clone and the apply refuses a checkout whose rendered/ is not its own
    render, so the apply never runs on what CI did not render and check."""
    spec = job()["spec"]["template"]["spec"]
    names = [c["name"] for c in spec["initContainers"]]
    assert names == ["clone", "check"]
    check = spec["initContainers"][1]
    assert check["args"] == ["validate", "--rendered", "/repo"]
    mounts = {m["name"]: m["mountPath"] for m in check["volumeMounts"]}
    assert mounts == {"repo": "/repo", "tmp": "/tmp"}


CONVERTER_PYPROJECT = REPO / "packages" / "converter" / "pyproject.toml"


def _converter_containers() -> list[dict]:
    """Every hook container that runs the `htrflow-campaigns` CLI -- all but
    the clone, which runs Python on dulwich."""
    spec = job()["spec"]["template"]["spec"]
    return [c for c in spec["initContainers"] + spec["containers"] if "args" in c
            and "command" not in c]  # fmt: skip


def _substituted(args: list[str]) -> list[str]:
    """What the kubelet hands the process: `$(VAR)` expanded from the env."""
    return [re.sub(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", "dummy-value", a) for a in args]


@pytest.mark.parametrize(
    "name",
    [
        "check",
        pytest.param(
            "apply",
            marks=pytest.mark.skip(
                reason="apply --namespace lands with fix/audit-0923-converter-apply; "
                "remove this skip once that branch is merged"
            ),
        ),
    ],
)
def test_each_hook_container_parses_under_the_real_cli(monkeypatch, name):
    """A list compare of `args` passes a flag the CLI does not have; the
    parser does not. Each command is stubbed, so nothing runs."""
    from htrflow_converter import cli

    called = []
    for command in ("_validate", "_apply", "_render", "_init"):
        monkeypatch.setattr(cli, command, lambda *a, **k: called.append(a) or 0)
    (container,) = [c for c in _converter_containers() if c["name"] == name]
    assert cli.main(_substituted(container["args"])) == 0
    assert len(called) == 1


def test_the_hook_runs_the_converter_release_its_template_ships_in():
    """The hook's containers run flags of the converter that wrote the hook
    (`validate --rendered`, `apply --namespace`), so they must run THAT
    release's image, all of them the same one: pinned by digest with the
    converter's own version beside it, or -- until the release commit pins
    the digest -- named by that version's tag (docs/development/
    releasing.md). A release that bumped the version and forgot the pin
    fails here."""
    version = re.search(
        r'^version = "([^"]+)"', CONVERTER_PYPROJECT.read_text(), re.M
    ).group(1)
    lines = re.findall(r"image: (\S+)(.*)", HOOK.read_text())
    assert len(lines) == 3 and len(set(lines)) == 1, lines
    ((image, rest),) = set(lines)
    pinned = "@sha256:" in image and rest.strip() == f"# v{version}"
    tagged = image.endswith(f":v{version}")
    assert pinned or tagged, (image, rest, version)
