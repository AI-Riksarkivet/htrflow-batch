"""Every image dockerfile must see the whole uv workspace graph.

`uv export`/`uv sync --frozen` read uv.lock, and uv.lock describes every
workspace member — so a dockerfile that bind-mounts only *some* members'
``pyproject.toml`` files fails the build the moment the member list changes.
It did: removing ``packages/reconciler`` (B63) left all three dockerfiles
mounting a path that no longer exists and every image build broke with
``"/packages/reconciler/pyproject.toml": not found``. Nothing in CI builds
these dockerfiles (dagger builds its own container graph), so this test is
the gate.

Since B63 Task 18 the wrapper has ONE dockerfile for both architectures
(`base-amd64` / `base-arm64`, selected by `FROM base-${TARGETARCH}`), so the
same bind-mount block serves both arches by construction — what needs
guarding instead is that the arm64 branch keeps the hard-won extras the GB10
needs, and that nothing in the build path ever asks for a foreign
platform: `uv` segfaults under `qemu-x86_64`, so both images are built on a
runner of their own architecture and never emulated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCKERFILES = [
    "htrflow-batch.dockerfile",
    "htrflow-web.dockerfile",
]
WRAPPER_DOCKERFILE = REPO / ".docker" / "htrflow-batch.dockerfile"
_BIND = re.compile(r"--mount=type=bind,source=(packages/[^,]+/pyproject\.toml),")

# The arm64 extras (see the dockerfile's own comments for why each exists):
# triton JIT-compiles CUDA utils at runtime, and TrOCR's slow tokenizer needs
# sentencepiece to convert. transformers is NOT here: it is installed for both
# architectures from the TRANSFORMERS_VERSION build arg below.
ARM64_EXTRAS = [
    "gcc",
    "libc6-dev",
    "python3.10-dev",
    "sentencepiece==",
]

# Build paths that must never cross-build: a `--platform` flag or a
# qemu/binfmt setup step is exactly how the wrapper image ends up emulated.
# Prose may of course NAME qemu — saying why not to is the point — so only
# the things that actually turn emulation on are matched.
BUILD_PATHS = [
    REPO / "Makefile",
    *sorted((REPO / ".dagger").glob("*.go")),
    *sorted((REPO / ".github" / "workflows").glob("*.yml")),
    *sorted((REPO / ".github" / "actions").rglob("*.yml")),
]
_EMULATION = re.compile(r"--platform|setup-qemu|qemu-user|binfmt", re.I)


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


@pytest.mark.parametrize("name", DOCKERFILES)
def test_the_publish_tag_is_baked_into_both_images(name: str) -> None:
    """The status page's header names what the operator deployed, which is the
    publish tag -- not any package's own version. Both images take it as a
    build arg, keep it as an env var the process can read and stamp it as the
    OCI version label, so an image can always be asked what it is."""
    text = (REPO / ".docker" / name).read_text()
    assert "ARG HTRFLOW_BATCH_VERSION=dev" in text
    assert "ENV HTRFLOW_BATCH_VERSION=${HTRFLOW_BATCH_VERSION}" in text
    assert 'org.opencontainers.image.version="${HTRFLOW_BATCH_VERSION}"' in text


def test_one_wrapper_dockerfile_for_both_arches() -> None:
    """One file, two base stages, the runtime stage picked by TARGETARCH."""
    assert not (REPO / ".docker" / "htrflow-batch-gpu-arm64.dockerfile").exists()
    text = WRAPPER_DOCKERFILE.read_text()
    assert "AS base-amd64" in text
    assert "AS base-arm64" in text
    assert "FROM base-${TARGETARCH} AS runtime" in text


def test_arm64_branch_keeps_the_extras_the_gb10_needs() -> None:
    text = WRAPPER_DOCKERFILE.read_text()
    guarded = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for extra in ARM64_EXTRAS:
        assert extra in guarded, f"arm64 extra missing from the wrapper image: {extra}"
    # every arm64-only step sits behind the TARGETARCH guard
    assert guarded.count('if [ "$TARGETARCH" = "arm64" ]') >= 2


def test_nothing_in_the_build_path_asks_for_a_foreign_platform() -> None:
    for path in BUILD_PATHS:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith(("#", "//")):  # prose may name qemu
                continue
            assert not _EMULATION.search(line), (
                f"{path.name}:{n} cross-builds: {line.strip()}"
            )


def test_both_base_stages_export_the_htrflow_base_revision():
    """`HTRFLOW_BASE_REVISION` is an OCI label the wrapper cannot read from
    inside the container, so each base stage also exports it as ENV, which
    the runtime stage inherits and `provenance.py` stamps into every ALTO."""
    text = WRAPPER_DOCKERFILE.read_text(encoding="utf-8")
    stages = re.split(r"^FROM ", text, flags=re.M)
    base = [s for s in stages if re.match(r".* AS base-(amd64|arm64)\n", s)]
    assert len(base) == 2
    for stage in base:
        assert "ENV HTRFLOW_BASE_REVISION=${HTRFLOW_BASE_REVISION}" in stage


def test_the_transformers_line_is_one_build_arg_every_build_path_can_set() -> None:
    """Two transformers lines exist because the models do not agree, and which
    line an image carries is a property of that image -- so it is one build arg
    with a default, installed once for both architectures, and every path that
    builds the wrapper can pass it. A path that cannot is a line that can only
    be built by hand-editing the dockerfile."""
    text = WRAPPER_DOCKERFILE.read_text()
    assert re.search(r"^ARG TRANSFORMERS_VERSION=\d", text, re.M), (
        "the dockerfile must default the transformers line, not require the arg"
    )
    pins = re.findall(r"transformers==([^\"'\s]+)", text)
    assert pins == ["${TRANSFORMERS_VERSION}"], (
        "the only transformers pin in the image is the build arg: a second, "
        f"literal one is a line nobody can change from outside — found {pins}"
    )
    # A line whose dependencies do not fit this venv must fail the build.
    assert "uv pip check --python /app/.venv/bin/python" in text

    makefile = (REPO / "Makefile").read_text()
    assert "--build-arg TRANSFORMERS_VERSION=$(TRANSFORMERS_VERSION)" in makefile

    dagger = (REPO / ".dagger" / "build.go").read_text()
    assert 'Name: "TRANSFORMERS_VERSION"' in dagger
    publish_go = (REPO / ".dagger" / "publish.go").read_text()
    assert "transformersVersion string" in publish_go  # the function's own arg
    assert "resolvedTag, transformersVersion)" in publish_go  # reaches the build

    publish = (REPO / ".github" / "workflows" / "publish.yml").read_text()
    assert "transformers_version:" in publish  # the dispatch input
    assert "--transformers-version" in publish  # the dagger-built architecture
    assert '"TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION}"' in publish  # the other
