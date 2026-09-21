"""Every image the release publishes must run on every node we deploy to.

`publish.yml` used to build the web image on one runner and push it under the
bare tag, while only the wrapper got per-architecture tags joined into a
manifest list. The result was a tag whose manifest names a single
architecture: a node of the other architecture answers a pull of it with
``ImagePullBackOff``, and the chart pins that digest.

So this is the shape gate on the workflow: whatever it publishes, it
publishes for both architectures, natively on a runner of each (never
emulated -- `test_dockerfile_workspace.py` guards the other half of that),
and every image ends the run as one manifest list under the plain tag that
the chart and the campaign pipelines reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
WORKFLOW = yaml.safe_load((WORKFLOWS / "publish.yml").read_text())
JOBS = WORKFLOW["jobs"]

#: An expression is substituted into the text before anything runs, so a
#: script that contains one is a script written partly by whoever supplied
#: the value. `secrets.*` and `env.*` are not here: a secret is masked and
#: redacted by the runner, and `env` is the destination this rule pushes
#: things towards.
_EXPRESSION = re.compile(r"\$\{\{\s*(inputs|steps)\.")

#: The workflows that build or publish an image. `with: args:` stays in the
#: scan although no step uses dagger-for-github any more: its `args` input
#: was not an argument list but script text the action pasted, unquoted, into
#: a `run:` of its own, and anything shaped like it would be again.
_SCRIPTED = ["publish.yml", "ci.yml", "security.yml"]

# Runner labels per architecture: a per-arch tag built on the wrong runner is
# a cross-build with extra steps.
RUNNERS = {"-amd64": "ubuntu-24.04", "-arm64": "ubuntu-24.04-arm"}


def _repository(image: str) -> str:
    """`docker.io/riksarkivet/htrflow-web` -> `riksarkivet/htrflow-web`."""
    return image.split("/", 1)[1]


def _published() -> dict[str, set[str]]:
    """Per-architecture tags the run pushes, as {repository: {suffix, …}}."""
    pushed: dict[str, set[str]] = {}
    for entry in JOBS["publish"]["strategy"]["matrix"]["include"]:
        pushed.setdefault(entry["repository"], set()).add(entry["tag_suffix"])
    # The wrapper's other architecture has a job of its own: it builds the
    # base image the dagger engine cannot see, and pushes `<tag>-arm64`.
    arm64 = JOBS["publish-wrapper-arm64"]
    pushed.setdefault(_repository(arm64["env"]["IMAGE"]), set()).add("-arm64")
    return pushed


def test_every_image_is_published_for_both_architectures() -> None:
    assert _published() == {
        "riksarkivet/htrflow-batch": set(RUNNERS),
        "riksarkivet/htrflow-web": set(RUNNERS),
    }


@pytest.mark.parametrize("entry", JOBS["publish"]["strategy"]["matrix"]["include"])
def test_each_matrix_build_runs_on_a_runner_of_its_own_architecture(
    entry: dict[str, str],
) -> None:
    assert JOBS["publish"]["runs-on"] == "${{ matrix.runner }}"
    assert entry["runner"] == RUNNERS[entry["tag_suffix"]]


def test_every_image_ends_as_one_manifest_list_under_the_plain_tag() -> None:
    """The tag the chart pins is the list, not one architecture's image."""
    manifest = JOBS["manifest"]
    listed = {e["repository"] for e in manifest["strategy"]["matrix"]["include"]}
    assert listed == set(_published())
    # Nothing may publish without being joined afterwards.
    assert set(manifest["needs"]) == set(JOBS) - {"manifest"}
    create = next(
        step["run"] for step in manifest["steps"] if "imagetools create" in str(step)
    )
    for suffix in RUNNERS:
        assert f'"${{IMAGE}}:${{TAG}}{suffix}"' in create


def test_every_pushed_digest_is_signed_and_attested() -> None:
    """Signature, provenance and SBOM come from the one composite action, so
    a new build job cannot quietly publish an unsigned image."""
    for name, job in JOBS.items():
        steps = [step.get("uses", "") for step in job["steps"]]
        assert "./.github/actions/sign-attest" in steps, name


def test_no_run_block_interpolates_a_dispatch_input() -> None:
    """`${{ inputs.tag }}` inside a `run:` is textual substitution into the
    script before any shell sees it, so whatever the dispatcher typed becomes
    code -- in jobs that hold the registry credential and the keyless signing
    identity. The tag pattern is checked by the first step, but that step is
    itself one of the interpolations, so it cannot be what protects them.

    Every use goes through the environment instead, where the shell treats
    it as data whatever it contains.
    """
    for name, job in JOBS.items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{ inputs." not in run, (name, step.get("name"))
            if "$TAG" in run or "${TAG}" in run:
                env = {**job.get("env", {}), **step.get("env", {})}
                assert env.get("TAG") == "${{ inputs.tag }}", name


@pytest.mark.parametrize("name", _SCRIPTED)
def test_no_script_carries_a_github_expression(name: str) -> None:
    """The narrower rule above covers `run:` blocks in publish.yml. This is
    the whole of it: `with: args:` on the dagger action is shell text too
    (the action pastes it unquoted into a `run:`), a step output is as much
    an outside value as a dispatch input, and ci.yml builds images from the
    same dockerfiles.

    Everything goes through `env:`, where the shell reads it as data
    whatever it contains.
    """
    workflow = yaml.safe_load((WORKFLOWS / name).read_text())
    for job, body in workflow["jobs"].items():
        for step in body["steps"]:
            scripts = {
                "run": step.get("run", ""),
                "args": str(step.get("with", {}).get("args", "")),
            }
            for where, script in scripts.items():
                found = _EXPRESSION.search(script)
                assert not found, (name, job, step.get("name"), where, found.group())


SETUP_DAGGER = "./.github/actions/setup-dagger"
ACTIONS = REPO / ".github" / "actions"


def _all_workflows() -> dict[str, dict]:
    return {p.name: yaml.safe_load(p.read_text()) for p in WORKFLOWS.glob("*.yml")}


def test_no_workflow_installs_dagger_through_a_piped_script() -> None:
    """Finding 3070: dagger/dagger-for-github installs the CLI with
    `curl https://dl.dagger.io/dagger/install.sh | sh`, in the publish jobs
    after the registry login and with the signing identity. The CLI comes
    from the checksum-verified setup action instead."""
    steps = [
        (name, step)
        for name, workflow in _all_workflows().items()
        for body in workflow["jobs"].values()
        for step in body.get("steps", [])
    ] + [
        (path.parent.name, step)
        for path in ACTIONS.rglob("action.yml")
        for step in yaml.safe_load(path.read_text())["runs"].get("steps", [])
    ]
    for where, step in steps:
        assert not step.get("uses", "").startswith("dagger/dagger-for-github"), where
        assert not re.search(r"curl[^\n]*\|\s*(ba)?sh\b", step.get("run", "")), where


def test_every_dagger_call_runs_after_the_pinned_setup() -> None:
    for name, workflow in _all_workflows().items():
        for job, body in workflow["jobs"].items():
            steps = body.get("steps", [])
            setup = [i for i, s in enumerate(steps) if s.get("uses") == SETUP_DAGGER]
            calls = [
                i
                for i, s in enumerate(steps)
                if re.search(r"^\s*(ref=\"\$\()?dagger ", s.get("run", ""), re.M)
            ]
            if calls:
                assert setup and setup[0] < calls[0], (name, job)


def test_the_setup_pins_the_cli_and_engine_of_dagger_json() -> None:
    engine = json.loads((REPO / "dagger.json").read_text())["engineVersion"]
    action = yaml.safe_load((ACTIONS / "setup-dagger" / "action.yml").read_text())
    env = action["runs"]["steps"][0]["env"]
    assert "v" + env["DAGGER_VERSION"] == engine
    for arch in ("AMD64", "ARM64"):
        assert re.fullmatch(r"[0-9a-f]{64}", env[f"SHA256_LINUX_{arch}"]), arch
    assert re.fullmatch(
        rf"registry\.dagger\.io/engine:{re.escape(engine)}@sha256:[0-9a-f]{{64}}",
        env["ENGINE_IMAGE"],
    )
    run = action["runs"]["steps"][0]["run"]
    assert "sha256sum --check" in run
    assert "_EXPERIMENTAL_DAGGER_RUNNER_HOST=docker-image://${ENGINE_IMAGE}" in run


def test_tools_are_installed_before_the_registry_login() -> None:
    """Nothing a publish job downloads after `docker login` may be unpinned:
    the dagger CLI is set up before the credential exists in the job."""
    for name, job in JOBS.items():
        uses = [s.get("uses", "") for s in job["steps"]]
        logins = [i for i, u in enumerate(uses) if u.startswith("docker/login-action")]
        if SETUP_DAGGER in uses:
            assert logins and uses.index(SETUP_DAGGER) < logins[0], name


def _step_index(steps: list[dict], needle: str) -> int:
    found = [i for i, s in enumerate(steps) if needle in s.get("run", "")]
    assert len(found) == 1, needle
    return found[0]


def test_the_arm64_wrapper_is_trivy_gated_before_it_is_pushed() -> None:
    """Finding 3060: only the amd64 wrapper went through Trivy. The arm64
    publish job now scans its image between the build and the push, and
    publish-docker gates the dagger-built images the same way."""
    steps = JOBS["publish-wrapper-arm64"]["steps"]
    build = _step_index(steps, "docker build -f")
    scan = _step_index(steps, "make scan-image")
    push = _step_index(steps, "docker push")
    assert build < scan < push
    assert '"${IMAGE}:${TAG}-arm64"' in steps[scan]["run"]

    publish_go = (REPO / ".dagger" / "publish.go").read_text()
    gate = publish_go.index('m.scanImage(ctx, container, "CRITICAL"')
    assert gate < publish_go.index(".Publish(ctx, imageRef)")


def test_the_arm64_wrapper_is_scanned_in_ci_and_every_week() -> None:
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())["jobs"]["build-arm64"]
    _step_index(ci["steps"], "make scan-image SCAN_IMAGE=htrflow-batch:ci-arm64")

    security = yaml.safe_load((WORKFLOWS / "security.yml").read_text())["jobs"]
    job = security["scan-wrapper-arm64"]
    assert job["runs-on"] == RUNNERS["-arm64"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("--format sarif" in r for r in runs)  # the Security tab report
    assert any(r.strip() == "make scan-image" for r in runs)  # the CRITICAL gate


def test_every_arm64_base_is_built_from_the_same_htrflow_commit() -> None:
    refs = set()
    for name in ("ci.yml", "publish.yml", "security.yml"):
        workflow = yaml.safe_load((WORKFLOWS / name).read_text())
        envs = [workflow.get("env", {})] + [
            j.get("env", {}) for j in workflow["jobs"].values()
        ]
        refs |= {
            e["HTRFLOW_ARM64_BASE_REF"] for e in envs if "HTRFLOW_ARM64_BASE_REF" in e
        }
    assert len(refs) == 1, refs
