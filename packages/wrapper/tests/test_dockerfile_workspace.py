"""Every image dockerfile must see the whole uv workspace graph.

`uv export`/`uv sync --frozen` read uv.lock, and uv.lock describes every
workspace member — so a dockerfile that bind-mounts only *some* members'
``pyproject.toml`` files fails the build the moment the member list changes.
It did: removing ``packages/reconciler`` (B63) left all three dockerfiles
mounting a path that no longer exists and every image build broke with
``"/packages/reconciler/pyproject.toml": not found``. Nothing in CI builds
these dockerfiles (dagger builds its own container graph), so this test is
the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCKERFILES = [
    "htrflow-batch.dockerfile",
    "htrflow-batch-gpu-arm64.dockerfile",
    "htrflow-api.dockerfile",
]
_BIND = re.compile(r"--mount=type=bind,source=(packages/[^,]+/pyproject\.toml),")


def _members() -> set[str]:
    return {
        f"packages/{p.parent.name}/pyproject.toml"
        for p in REPO.glob("packages/*/pyproject.toml")
    }


@pytest.mark.parametrize("name", DOCKERFILES)
def test_binds_every_workspace_member(name: str) -> None:
    text = (REPO / ".docker" / name).read_text()
    assert set(_BIND.findall(text)) == _members()


@pytest.mark.parametrize("name", DOCKERFILES)
def test_no_stale_copy_of_a_removed_member(name: str) -> None:
    text = (REPO / ".docker" / name).read_text()
    copied = re.findall(r"^COPY (packages/\S+/pyproject\.toml)", text, re.M)
    assert set(copied) <= _members()
