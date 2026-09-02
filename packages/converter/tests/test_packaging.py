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
