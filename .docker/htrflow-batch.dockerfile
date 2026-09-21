# htrflow-batch: the streaming wrapper (docs: how-it-works/wrapper) on top
# of the stock htrflow image, for BOTH architectures. Build context = repo root.
#
# One file, two bases, no emulation. `uv` segfaults under `qemu-x86_64` and
# the GPU never crosses the emulation boundary anyway (a 2-page volume:
# ~55 s native vs 1 h+ emulated on CPU), so each architecture is built on a
# machine of its own: `docker build` picks `base-${TARGETARCH}` from the
# host it runs on and nothing here ever passes `--platform`.
#
#   amd64  the published upstream image, digest-pinned, plus a torch swap
#          for cu128 wheels that carry Blackwell (sm_120) kernels.
#   arm64  a locally built htrflow base (the upstream image is amd64-only),
#          plus the compiler the GB10 needs — see the guarded steps below.
#          Build it first, from a checkout of AI-Riksarkivet/htrflow, with
#          this repository's .docker/htrflow-base.dockerfile and the
#          lockfile committed beside it (`make build-htrflow-base-arm64`).
#          `make build-wrapper` on an aarch64 host and the arm64 wrapper
#          jobs in publish.yml/ci.yml/security.yml all build exactly this
#          stage.
#
# Reproducibility (audit W8/S7, finding 3060): every input is pinned.
#   * the amd64 base image and the uv binary are pinned by digest, and so
#     is everything the arm64 base is built from, down to its lockfile;
#   * torch/torchvision are pinned per arch. amd64: the versions the
#     floating cu128 `--upgrade` resolved to on 2026-08-26 (the upstream
#     base ships torch 2.6.0/torchvision 0.21.0 cu12x; the cu128 index
#     carries cp310 wheels up to 2.9.1/0.24.1). arm64: what the arm64
#     base's committed lock installs from PyPI, whose aarch64 wheels bundle
#     CUDA 13 — torch reports 2.13.0+cu130 and runs on the GB10. The cu128
#     swap is a no-op on that arch (no cp310 wheel newer than 2.9.1), so
#     the arm64 branch only asserts the versions, and a refreshed base lock
#     that moves them fails the build instead of changing the image;
#   * the wrapper's dependencies, the transformers line and the leaf
#     overrides come from the workspace lock (`uv export` with hashes), not
#     a free resolution at build time;
#   * apt packages stay unpinned: Ubuntu's archive drops superseded
#     versions, so an exact apt pin breaks the build on the next security
#     update (a snapshot mirror is the real fix, out of scope here).
# Refreshing a pin: `docker buildx imagetools inspect <ref>` for digests;
# https://download.pytorch.org/whl/cu128/torch/ for torch versions.
#
# Build args:
#   HTRFLOW_ARM64_BASE     the local arm64 base image tag. A local tag has no
#                          registry digest to pin — HTRFLOW_BASE_REVISION is
#                          how the image records what it really contains.
#   HTRFLOW_BASE_REVISION  `git describe --tags --always --dirty` of the
#                          htrflow checkout the base was built from; stamped
#                          into the `se.riksarkivet.htrflow.base.revision`
#                          label so the image says which htrflow it really
#                          runs (manifest.json only knows the package
#                          version, "0.2.6", and the arm64 base is built well
#                          past that tag). Each base stage declares its own
#                          default, so an un-passed arg still tells the truth.
ARG HTRFLOW_ARM64_BASE=htrflow:v0.2.6-arm64

# Both base stages carry their provenance labels; the runtime stage inherits
# whichever one it is built FROM. BuildKit resolves only the stage the target
# needs, so the amd64-only upstream image is never even looked up on arm64.
FROM airiksarkivet/htrflow:v0.2.6-35f48a7@sha256:e56a87f7ad2b9d4fd87dcbed32bfa56cb0ba7cddfcca97ebf0045b77462695de AS base-amd64
ARG HTRFLOW_BASE_REVISION=v0.2.6-35f48a7
LABEL org.opencontainers.image.base.name="docker.io/airiksarkivet/htrflow:v0.2.6-35f48a7" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"
# Also as ENV: a label is invisible from inside the container, and the
# wrapper stamps this into every ALTO (provenance.py).
ENV HTRFLOW_BASE_REVISION=${HTRFLOW_BASE_REVISION}

FROM ${HTRFLOW_ARM64_BASE} AS base-arm64
ARG HTRFLOW_ARM64_BASE
ARG HTRFLOW_BASE_REVISION=unknown
LABEL org.opencontainers.image.base.name="${HTRFLOW_ARM64_BASE}" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"
# Also as ENV: a label is invisible from inside the container, and the
# wrapper stamps this into every ALTO (provenance.py).
ENV HTRFLOW_BASE_REVISION=${HTRFLOW_BASE_REVISION}

FROM base-${TARGETARCH} AS runtime
LABEL org.opencontainers.image.licenses="EUPL-1.2"
ARG TARGETARCH

# uv 0.12.6 (multi-arch index digest)
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# The base's Ubuntu packages lag behind jammy-security (gnupg and openssl
# carry Trivy HIGH findings that Ubuntu has already fixed). Upgrade from the
# Ubuntu archive only: SourceParts=/dev/null hides /etc/apt/sources.list.d,
# where the NVIDIA CUDA repository lives, so no CUDA, driver or cuDNN
# package can move with it.
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update -o Dir::Etc::SourceParts=/dev/null \
    && apt-get upgrade -y -o Dir::Etc::SourceParts=/dev/null \
    && rm -rf /var/lib/apt/lists/*

# torch: amd64 swaps in cu128 builds (Blackwell sm_120 kernels); on arm64
# the base's committed lock already installed it (CUDA 13 aarch64), so the
# versions are only checked here.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      /app/.venv/bin/python -c 'import sys, torch, torchvision; \
have = (torch.__version__.split("+")[0], torchvision.__version__.split("+")[0]); \
sys.exit(None if have == ("2.13.0", "0.28.0") else \
         f"arm64 base carries torch/torchvision {have}, not 2.13.0/0.28.0: its lock moved")'; \
    else \
      uv pip install --python /app/.venv/bin/python --no-cache \
        --index-url https://download.pytorch.org/whl/cu128 \
        "torch==2.9.1" "torchvision==0.24.1"; \
    fi

# The wrapper is a uv workspace member (packages/wrapper). It is installed
# with `uv pip install` into the base image's existing /app/.venv rather than
# with the workspace `uv sync` (ra-skills dockerfile/references/python-uv.md):
# that venv already carries htrflow and the torch installed above, and
# `uv sync` would prune it back to the lockfile's contents, removing exactly
# the packages this image exists for. Its dependencies are still the LOCKED
# ones: `uv export` renders the wrapper's subtree of uv.lock (pinned, hashed)
# and that is what gets installed; the package itself goes in with --no-deps.
# Bind-mount EVERY workspace member's pyproject.toml — uv needs the whole
# workspace graph to read the lock.
RUN --mount=type=bind,source=uv.lock,target=/opt/workspace/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/opt/workspace/pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=/opt/workspace/packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=/opt/workspace/packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=/opt/workspace/packages/web/pyproject.toml \
    cd /opt/workspace \
    && uv export --locked --package htrflow-batch-wrapper --no-dev --no-emit-project \
         -o /tmp/wrapper-requirements.txt \
    && uv pip install --python /app/.venv/bin/python --no-cache --require-hashes \
         -r /tmp/wrapper-requirements.txt \
    && rm /tmp/wrapper-requirements.txt
COPY packages/wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache --no-deps /opt/wrapper

# arm64 only: triton JIT-compiles its CUDA utils (a CPython extension) at
# runtime, so it needs a C compiler and Python headers or TrOCR generation
# dies with "Failed to find C compiler" on the GPU path. The locally built
# base does not carry them.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        gcc libc6-dev python3.10-dev \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# The transformers line, both architectures, pinned here so the image says
# which one it runs. Two lines exist because the models do not agree: a
# model saved by transformers 5 (its tokenizer config carries keys 4.x cannot
# read, and 4.x decodes its byte-level tokenizer wrongly) needs 5.x, while
# a model saved by 4.x (the base handwritten models, whose positional
# embedding buffer 5.x leaves on the meta device) needs 4.x until it is
# re-saved. A pipeline pins the image digest it runs, so one campaigns repo
# can carry pipelines on either line. Default: the 4.x line upstream htrflow
# is tested on; `make build-wrapper TRANSFORMERS_VERSION=5.9.0` builds the
# other.
#
# Each line is a dependency group of the workspace (`transformers-<major>`
# in the root pyproject.toml), locked with its whole closure: sentencepiece
# (arm64: TrOCR's slow tokenizer needs it to convert, and 5.x dropped that
# conversion) and protobuf (transformers only imports it on the error path
# of loading a slow tokenizer, and without it that path reports "requires
# the protobuf library" INSTEAD of the real error). The build installs the
# group's exported, hashed closure, so nothing here is resolved at build
# time; a version the lock does not carry fails the build.
ARG TRANSFORMERS_VERSION=4.57.6
RUN --mount=type=bind,source=uv.lock,target=/opt/workspace/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/opt/workspace/pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=/opt/workspace/packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=/opt/workspace/packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=/opt/workspace/packages/web/pyproject.toml \
    cd /opt/workspace \
    && uv export --locked --only-group "transformers-${TRANSFORMERS_VERSION%%.*}" --no-emit-project \
         -o /tmp/transformers-requirements.txt \
    && { grep -q "^transformers==${TRANSFORMERS_VERSION} " /tmp/transformers-requirements.txt \
         || { echo "TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION} is not the version uv.lock pins" \
                   "for its line (pyproject.toml, group transformers-${TRANSFORMERS_VERSION%%.*})"; \
              exit 1; }; } \
    && uv pip install --python /app/.venv/bin/python --no-cache --require-hashes \
         -r /tmp/transformers-requirements.txt \
    && rm /tmp/transformers-requirements.txt

# Packages of the base's venv with published fixes that htrflow's own lock
# predates: pillow and Brotli. The `wrapper-image` group in uv.lock pins them
# (pinned, hashed), and they go in last so they are the versions that survive.
# Both are leaves (--no-deps). Not here: py7zr, which pagexml-tools caps below
# 0.21, and transformers, which has a step and a build argument of its own
# above; py7zr waits for htrflow.
RUN --mount=type=bind,source=uv.lock,target=/opt/workspace/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/opt/workspace/pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=/opt/workspace/packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=/opt/workspace/packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=/opt/workspace/packages/web/pyproject.toml \
    cd /opt/workspace \
    && uv export --locked --only-group wrapper-image --no-emit-project \
         -o /tmp/image-requirements.txt \
    && uv pip install --python /app/.venv/bin/python --no-cache --no-deps --require-hashes \
         -r /tmp/image-requirements.txt \
    && rm /tmp/image-requirements.txt

# What this image must guarantee, after the transformers line has had its say:
# the WRAPPER's own declared requirements are satisfied by what is installed.
# The newer transformers line requires a newer huggingface_hub than the wrapper
# used to accept, and that mismatch belongs at build time, not in a warm-up pod
# -- nothing in CI builds this image, so this is the only gate.
#
# Not `uv pip check`, which validates every distribution in the venv: one base
# venv is already inconsistent for a reason that has nothing to do with this
# image (its torch pin drags in an nvidia wheel built for another platform),
# so the broad check blocks every build on that architecture, including the
# default line. This one reads the wrapper's own metadata and nothing else.
RUN /app/.venv/bin/python <<'CHECK'
import sys
from importlib.metadata import PackageNotFoundError, requires, version

from packaging.requirements import Requirement

bad = []
for spec in requires("htrflow-batch-wrapper") or []:
    req = Requirement(spec)
    if req.marker and not req.marker.evaluate({"extra": ""}):
        continue
    try:
        have = version(req.name)
    except PackageNotFoundError:
        bad.append(f"{spec}: not installed")
        continue
    if not req.specifier.contains(have, prereleases=True):
        bad.append(f"{spec}: installed {have}")
if bad:
    sys.exit("the wrapper's requirements are not satisfied:\n  " + "\n  ".join(bad))
print("wrapper requirements satisfied")
CHECK

# The release this image is published under: the publish workflow passes its
# run tag, `make build-*` passes IMAGE_TAG, and a build that passes nothing
# says "dev". Kept as an env var because the process itself reports it (the
# status page's header shows what the operator deployed, which is this tag
# and not any package's own version), and as the OCI label so an image on a
# registry can be asked the same question without running it.
ARG HTRFLOW_BATCH_VERSION=dev
ENV HTRFLOW_BATCH_VERSION=${HTRFLOW_BATCH_VERSION}
LABEL org.opencontainers.image.version="${HTRFLOW_BATCH_VERSION}"

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
# Writable paths (HOME, TMPDIR, YOLO_CONFIG_DIR) are set by the Job spec
# into the tmpfs workdir; the root filesystem is mounted read-only.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
