"""Every image dockerfile must see the whole uv workspace graph.

`uv export`/`uv sync --frozen` read uv.lock, and uv.lock describes every
workspace member — so a dockerfile that bind-mounts only *some* members'
``pyproject.toml`` files fails the build the moment the member list changes.
It did: removing ``packages/reconciler`` (B63) left all three dockerfiles
mounting a path that no longer exists and every image build broke with
``"/packages/reconciler/pyproject.toml": not found``. Nothing in CI builds
these dockerfiles (dagger builds its own container graph), so this test is
the gate.

The wrapper has ONE dockerfile, and one recipe, for both architectures: it
builds its own htrflow base from pinned sources, so the same bind-mount
block serves both arches by construction — what needs guarding instead is
that the base stays pinned, that the arm64 step keeps the hard-won extras
the GB10 needs, and that nothing in the build path ever asks for a foreign
platform: `uv` segfaults under `qemu-x86_64`, so both images are built on a
runner of their own architecture and never emulated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib
import yaml

REPO = Path(__file__).resolve().parents[3]
DOCKERFILES = [
    "htrflow-batch.dockerfile",
    "htrflow-web.dockerfile",
    "htrflow-campaigns.dockerfile",
]
WRAPPER_DOCKERFILE = REPO / ".docker" / "htrflow-batch.dockerfile"
_BIND = re.compile(r"--mount=type=bind,source=(packages/[^,]+/pyproject\.toml),")

# The arm64 extras (see the dockerfile's own comments for why each exists):
# triton JIT-compiles CUDA utils at runtime. sentencepiece, which TrOCR's slow
# tokenizer needs to convert, is not here: it is locked with the transformers
# line (see test_every_transformers_line_is_a_locked_group).
ARM64_EXTRAS = [
    "gcc",
    "libc6-dev",
    "python3.10-dev",
]
HTRFLOW_BASE = REPO / ".docker" / "htrflow-base"

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


def test_campaigns_image_is_distroless_nonroot_and_locked():
    text = (REPO / ".docker/htrflow-campaigns.dockerfile").read_text()
    assert "gcr.io/distroless/python3-debian13:nonroot@sha256:" in text
    assert (
        "uv sync --locked --package htrflow-converter --extra hook --no-editable"
        in text
    )
    assert "USER 1000:1000" in text
    assert 'ENTRYPOINT ["/app/.venv/bin/htrflow-campaigns"]' in text


def test_one_wrapper_dockerfile_one_base_for_both_arches() -> None:
    """One file and one htrflow base for both architectures. The amd64 image
    used to run on the upstream htrflow image, whose htrflow is older than
    the API the driver drives, and the arm64 one on a base built elsewhere
    that the dagger engine could not see."""
    assert not (REPO / ".docker" / "htrflow-batch-gpu-arm64.dockerfile").exists()
    assert not (REPO / ".docker" / "htrflow-base.dockerfile").exists()
    text = WRAPPER_DOCKERFILE.read_text()
    assert "FROM htrflow-base AS runtime" in text
    assert "airiksarkivet/htrflow" not in text
    assert "base-${TARGETARCH}" not in text


def test_arm64_branch_keeps_the_extras_the_gb10_needs() -> None:
    text = WRAPPER_DOCKERFILE.read_text()
    guarded = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for extra in ARM64_EXTRAS:
        assert extra in guarded, f"arm64 extra missing from the wrapper image: {extra}"
    # the arm64-only step sits behind the TARGETARCH guard
    assert guarded.count('if [ "$TARGETARCH" = "arm64" ]') == 1


def test_nothing_in_the_build_path_asks_for_a_foreign_platform() -> None:
    for path in BUILD_PATHS:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith(("#", "//")):  # prose may name qemu
                continue
            assert not _EMULATION.search(line), (
                f"{path.name}:{n} cross-builds: {line.strip()}"
            )


def test_the_base_stage_exports_the_htrflow_base_revision():
    """`HTRFLOW_BASE_REVISION` is an OCI label the wrapper cannot read from
    inside the container, so the base stage also exports it as ENV, which
    the runtime stage inherits and `provenance.py` stamps into every ALTO.
    Unset, it is the htrflow commit the base is built from."""
    text = WRAPPER_DOCKERFILE.read_text(encoding="utf-8")
    stages = re.split(r"^FROM ", text, flags=re.M)
    base = [s for s in stages if re.match(r".* AS htrflow-base\n", s)]
    assert len(base) == 1
    assert "ARG HTRFLOW_BASE_REVISION=${HTRFLOW_REF}" in base[0]
    assert "ENV HTRFLOW_BASE_REVISION=${HTRFLOW_BASE_REVISION}" in base[0]


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
    # A transformers line whose dependencies do not fit the WRAPPER's own
    # requirements must fail the build, not a warm-up pod. Deliberately not
    # `uv pip check`: a base venv carries inconsistencies of its own that have
    # nothing to do with this image, and they must not block its builds -- the
    # dockerfile's comment may say so, but no step may run it.
    assert not re.search(r"^\s*RUN.*uv pip check", text, re.M)
    assert re.search(r'for dist in \("htrflow-batch-wrapper", "transformers"', text)
    assert "req.specifier.contains(have" in text

    makefile = (REPO / "Makefile").read_text()
    assert "--build-arg TRANSFORMERS_VERSION=$(TRANSFORMERS_VERSION)" in makefile

    dagger = (REPO / ".dagger" / "build.go").read_text()
    assert 'Name: "TRANSFORMERS_VERSION"' in dagger
    publish_go = (REPO / ".dagger" / "publish.go").read_text()
    assert "transformersVersion string" in publish_go  # the function's own arg
    assert "resolvedTag, transformersVersion)" in publish_go  # reaches the build

    publish = (REPO / ".github" / "workflows" / "publish.yml").read_text()
    assert "transformers_version:" in publish  # the dispatch input
    assert "--transformers-version" in publish  # every image is dagger-built


def test_the_library_api_pin_runs_against_the_image_ci_builds() -> None:
    """`test_driver_real.py` is the canary for an htrflow release that
    changes the library API the driver drives -- and no workflow ran it. Both
    ways to run it (`dagger call test-driver`, `make test-driver-real`) were
    things a person had to remember, and nobody did, so the pin protected
    nothing.

    It runs in the job that has already built the wrapper image, through
    `make`: `dagger call test-driver` cannot serve there, because the dagger
    engine builds in its own cache and cannot see a base image that exists
    only in that runner's docker daemon.
    """
    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
    job = ci["jobs"]["build-arm64"]
    built = [s for s in job["steps"] if "docker build" in s.get("run", "")]
    assert len(built) == 1
    tag = re.search(r"-t (\S+)", built[0]["run"]).group(1)

    driver = [s for s in job["steps"] if "test-driver-real" in s.get("run", "")]
    assert len(driver) == 1, "the level-0 pin runs in no workflow"
    # Against the image this job built, not one it would have to pull.
    assert f"WRAPPER_IMAGE={tag}" in driver[0]["run"]
    assert job["steps"].index(driver[0]) > job["steps"].index(built[0])


def _logical_lines(text: str) -> list[str]:
    """Dockerfile instructions with their continuation lines joined."""
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return code.replace("\\\n", " ").splitlines()


def test_every_python_install_in_the_wrapper_image_is_locked() -> None:
    """Finding 3060: sentencepiece, transformers and protobuf went in by a
    bare version pin, so their dependencies resolved afresh at every build
    and nothing checked a hash. Every `uv pip install` now installs a
    hashed `uv export` of uv.lock or a hashed requirements file, or this
    repo's own wrapper with --no-deps. The amd64 torch swap from the cu128
    index, the last exception, is gone: torch comes from the base's lock."""
    installs = [
        part
        for line in _logical_lines(WRAPPER_DOCKERFILE.read_text())
        for part in line.split("&&")
        if "uv pip install" in part
    ]
    assert installs
    for install in installs:
        assert "--require-hashes" in install or install.rstrip().endswith(
            "--no-deps /opt/wrapper"
        ), install


def test_every_transformers_line_is_a_hashed_requirements_file() -> None:
    """The TRANSFORMERS_VERSION build arg selects .docker/transformers/<major>.txt,
    compiled with hashes from the .in file beside it (`make
    transformers-requirements`). Each pins transformers exactly and carries
    the packages the line needs, and the dockerfile's default is one of them."""
    lines = REPO / ".docker" / "transformers"
    majors = {p.stem for p in lines.glob("*.in")}
    assert majors == {"4", "5"}
    for major in majors:
        compiled = (lines / f"{major}.txt").read_text()
        pins = re.findall(r"^([a-z0-9-]+)==(\S+)", compiled, re.M)
        names = {name for name, _ in pins}
        assert {
            "transformers",
            "huggingface-hub",
            "protobuf",
            "sentencepiece",
            "tokenizers",
        } <= names
        assert dict(pins)["transformers"].startswith(f"{major}.")
        # every pin carries its hashes, and the .txt is the .in compiled
        for name, ver in pins:
            assert re.search(
                rf"^{re.escape(name)}=={re.escape(ver)}[^\n]*\\\n\s+--hash=sha256:",
                compiled,
                re.M,
            ), name
        wanted = re.findall(
            r"^([a-z0-9-]+==\S+)", (lines / f"{major}.in").read_text(), re.M
        )
        assert sorted(wanted) == sorted(f"{n}=={v}" for n, v in pins)
    assert (
        "sentencepiece==0.2.2 ; platform_machine == 'aarch64'"
        in (lines / "4.txt").read_text()
    )
    default = re.search(
        r"^ARG TRANSFORMERS_VERSION=(\S+)", WRAPPER_DOCKERFILE.read_text(), re.M
    ).group(1)
    assert f"transformers=={default} " in (lines / f"{default[0]}.txt").read_text()


def test_the_htrflow_base_is_built_from_pinned_inputs() -> None:
    """Findings 3060 and 3104: the arm64 base came from htrflow's own
    dockerfile (CUDA image by tag, uv by `latest`) after a fresh `uv lock`,
    and the amd64 one was the upstream image with an older htrflow. Both are
    now stages of the wrapper dockerfile: htrflow by commit, every image by
    digest, synced --locked from the lock committed in .docker/htrflow-base/
    -- which is refused unless the checkout's pyproject.toml is the one it
    was made for."""
    text = WRAPPER_DOCKERFILE.read_text()
    froms = [
        ref
        for ref in re.findall(r"^FROM (\S+)", text, re.M)
        if ref not in ("scratch", "htrflow-base")
    ] + re.findall(r"COPY --from=(\S+:\S+)", text)
    assert froms
    for ref in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), ref
    ref = re.search(r"^ARG HTRFLOW_REF=([0-9a-f]{40})$", text, re.M)
    assert ref, "htrflow must be pinned by full commit"
    assert "ADD https://github.com/AI-Riksarkivet/htrflow.git#${HTRFLOW_REF} /" in text
    syncs = re.findall(r"^RUN uv sync.*$", text, re.M)
    assert len(syncs) == 2 and all("--locked" in s for s in syncs), syncs
    assert "cmp -s - /app/pyproject.toml" in text

    # The committed pyproject.toml is htrflow's plus the overlay, and the
    # overlay locks torch per architecture from the right index.
    pyproject = (HTRFLOW_BASE / "pyproject.toml").read_text()
    overlay = (HTRFLOW_BASE / "overlay.toml").read_text()
    assert pyproject.endswith(overlay)
    assert tomllib.loads(pyproject)["project"]["name"] == "htrflow"
    uv = tomllib.loads(overlay)["tool"]["uv"]
    assert "torch==2.9.1; platform_machine == 'x86_64'" in uv["constraint-dependencies"]
    assert uv["index"] == [
        {
            "name": "pytorch-cu128",
            "url": "https://download.pytorch.org/whl/cu128",
            "explicit": True,
        }
    ]
    lock = (HTRFLOW_BASE / "uv.lock").read_text()
    assert 'version = "2.9.1+cu128"' in lock and 'version = "2.13.0"' in lock

    # Nothing builds a base anywhere else any more.
    assert not (REPO / ".github" / "actions" / "build-htrflow-base-arm64").exists()
    for path in BUILD_PATHS:
        assert "HTRFLOW_ARM64_BASE" not in path.read_text(), path.name
