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

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = yaml.safe_load((REPO / ".github" / "workflows" / "publish.yml").read_text())
JOBS = WORKFLOW["jobs"]

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
        assert f'"${{IMAGE}}:${{{{ inputs.tag }}}}{suffix}"' in create


def test_every_pushed_digest_is_signed_and_attested() -> None:
    """Signature, provenance and SBOM come from the one composite action, so
    a new build job cannot quietly publish an unsigned image."""
    for name, job in JOBS.items():
        steps = [step.get("uses", "") for step in job["steps"]]
        assert "./.github/actions/sign-attest" in steps, name
