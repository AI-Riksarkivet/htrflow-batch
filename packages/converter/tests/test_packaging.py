"""render.py reads its manifest skeletons via importlib.resources at
runtime (render._load), not a relative filesystem path -- this only proves
itself against an *installed* package, so it builds the real wheel and
installs it into an isolated venv, far from the source tree's own
manifests/ directory (pyproject.toml's [tool.hatch.build.targets.wheel]
carries the ``artifacts`` pin that ships them)."""

import shutil
import subprocess
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).parents[3]
PACKAGE = WORKSPACE_ROOT / "packages" / "converter" / "src" / "htrflow_converter"
TEMPLATE = PACKAGE / "template"
# The CI flavour `init` writes by default, over the template.
CI_GITHUB = PACKAGE / "ci" / "github"
EXAMPLES_CAMPAIGNS = WORKSPACE_ROOT / "examples" / "campaigns"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_manifests_are_readable_via_importlib_resources_from_an_installed_wheel(
    tmp_path,
):
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--package", "htrflow-converter", "--out-dir", str(dist)],
        cwd=WORKSPACE_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("*.whl"))

    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    # -I: isolated (no cwd/user-site leakage); cwd is tmp_path, nowhere near
    # packages/converter/src/htrflow_converter/manifests/ -- if _load ever
    # regressed to a path relative to the source tree, this would fail with
    # FileNotFoundError instead of silently reading the repo's own copy.
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from htrflow_converter import render\n"
            "d = render._load('configmap.yaml')\n"
            "print(d['kind'])",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ConfigMap"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_template_dotfile_is_readable_from_an_installed_wheel(tmp_path):
    """``init`` needs the whole template and both CI flavours, dotfiles
    included -- ``ci/github/.github/workflows/render.yml`` is the one dotted
    path, ``ci/azure/azure-pipelines.yml`` the other flavour, and
    ``template/argocd/apply.yaml`` the hook manifest a repo's Argo CD runs. Verified
    by hand that hatchling's ``artifacts`` glob does carry a dot-directory
    into both the sdist and the wheel (unzip -l'd the built wheel); this
    pins that against a regression the same way the manifests test above
    does."""
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--package", "htrflow-converter", "--out-dir", str(dist)],
        cwd=WORKSPACE_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("*.whl"))

    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "from importlib import resources\n"
            "r = resources.files('htrflow_converter')\n"
            "for p in (r / 'ci' / 'github' / '.github' / 'workflows' / 'render.yml',\n"
            "          r / 'ci' / 'azure' / 'azure-pipelines.yml',\n"
            "          r / 'template' / 'argocd' / 'apply.yaml'):\n"
            "    print(len(p.read_text()))",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    sizes = [int(n) for n in result.stdout.split()]
    assert len(sizes) == 3 and all(sizes), result.stdout


def test_examples_match_template():
    """``examples/campaigns/`` is a generated, checked-in copy of the
    packaged template (Task 16) -- kept only so the docs' example is
    browsable on GitHub without installing anything. If this fails, run
    `htrflow-campaigns init --force examples/campaigns` from the repo root
    and commit the result."""
    hint = (
        "run `htrflow-campaigns init --force examples/campaigns` and commit the result"
    )
    # What `init` writes by default: the template plus the GitHub CI flavour.
    sources = {
        p.relative_to(root): p
        for root in (TEMPLATE, CI_GITHUB)
        for p in root.rglob("*")
        if p.is_file()
    }
    template_files = set(sources)
    example_files = {
        p.relative_to(EXAMPLES_CAMPAIGNS)
        for p in EXAMPLES_CAMPAIGNS.rglob("*")
        if p.is_file()
    }
    only_in_template = template_files - example_files
    only_in_examples = example_files - template_files
    assert not only_in_template, (
        f"missing from examples/campaigns: {only_in_template} -- {hint}"
    )
    assert not only_in_examples, (
        f"extra in examples/campaigns: {only_in_examples} -- {hint}"
    )
    for rel in sorted(template_files):
        assert sources[rel].read_bytes() == (EXAMPLES_CAMPAIGNS / rel).read_bytes(), (
            f"{rel} differs from the template -- {hint}"
        )
