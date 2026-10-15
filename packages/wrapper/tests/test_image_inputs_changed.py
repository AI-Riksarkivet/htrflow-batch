"""Which pushes to main rebuild and scan an image (scripts/image-inputs-changed.sh,
ci.yml's `changes` job): only one that touches what the image is built from,
and anything this list does not know counts as touching both."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "image-inputs-changed.sh"


def _changed(*paths: str) -> dict[str, str]:
    out = subprocess.run(
        ["bash", str(SCRIPT)],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return dict(line.split("=", 1) for line in out.split())


@pytest.mark.parametrize(
    "path, wrapper, web",
    [
        ("packages/wrapper/src/htrflow_batch/store.py", "true", "false"),
        (".docker/htrflow-base/uv.lock", "true", "true"),
        (".docker/transformers.txt", "true", "true"),
        ("uv.lock", "true", "true"),
        ("pyproject.toml", "true", "true"),
        ("packages/converter/pyproject.toml", "true", "true"),
        (".dagger/main.go", "true", "true"),
        (".github/workflows/ci.yml", "true", "true"),
        ("scripts/image-inputs-changed.sh", "true", "true"),
        ("frontend/src/routes/+page.svelte", "false", "true"),
        ("packages/web/src/htrflow_web/app.py", "false", "true"),
        ("docs/reference/s3-layout.md", "false", "false"),
        ("charts/htrflow-batch/values.yaml", "false", "false"),
        ("packages/converter/src/htrflow_converter/models.py", "false", "false"),
        ("scripts/loc-budget.sh", "false", "false"),
        ("README.md", "false", "false"),
    ],
)
def test_each_path_reaches_the_images_built_from_it(path, wrapper, web):
    assert _changed(path) == {"wrapper": wrapper, "web": web}


def test_one_input_among_unrelated_paths_is_enough():
    assert _changed("docs/a.md", "packages/wrapper/x.py", "README.md") == {
        "wrapper": "true",
        "web": "false",
    }


def test_no_paths_reach_no_image():
    assert _changed() == {"wrapper": "false", "web": "false"}
