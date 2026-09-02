# htrflow-batch: the D16 streaming wrapper (DESIGN.md §5.1) on top of the
# stock htrflow image, for BOTH architectures. Build context = repo root.
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
#          plus three extras the GB10 needs — see the guarded steps below.
#          Build it first, from a checkout of AI-Riksarkivet/htrflow:
#            uv lock && docker build -f docker/htrflow.dockerfile \
#              -t htrflow:v0.2.6-arm64 .
#          (the lockfile is gitignored there). `make build-wrapper` on an
#          aarch64 host and the `wrapper-arm64` jobs in publish.yml/ci.yml
#          both build exactly this stage.
#
# Reproducibility (audit W8/S7): every input is pinned.
#   * the amd64 base image and the uv binary are pinned by digest;
#   * torch/torchvision are pinned per arch. amd64: the versions the
#     floating cu128 `--upgrade` resolved to on 2026-08-26 (the upstream
#     base ships torch 2.6.0/torchvision 0.21.0 cu12x; the cu128 index
#     carries cp310 wheels up to 2.9.1/0.24.1). arm64: the versions the
#     base's own lock resolves from PyPI, whose aarch64 wheels bundle CUDA
#     13 — torch reports 2.13.0+cu130 and runs on the GB10. The cu128 swap
#     is a no-op on that arch (no cp310 wheel newer than 2.9.1), so the
#     arm64 branch pins explicitly instead, and the build fails if the
#     base's lock drifts;
#   * the wrapper's dependencies come from the workspace lock (`uv export`
#     with hashes), not a free resolution at build time;
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

FROM ${HTRFLOW_ARM64_BASE} AS base-arm64
ARG HTRFLOW_ARM64_BASE
ARG HTRFLOW_BASE_REVISION=unknown
LABEL org.opencontainers.image.base.name="${HTRFLOW_ARM64_BASE}" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"

FROM base-${TARGETARCH} AS runtime
ARG TARGETARCH

# uv 0.12.6 (multi-arch index digest)
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# torch: amd64 swaps in cu128 builds (Blackwell sm_120 kernels); arm64 pins
# what the base's own lock already resolved from PyPI (CUDA 13 aarch64).
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      uv pip install --python /app/.venv/bin/python --no-cache \
        "torch==2.13.0" "torchvision==0.28.0"; \
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

# arm64 only, and after the wrapper install so these versions are the ones
# that survive. Three extras the locally built base does not carry:
#   * triton JIT-compiles its CUDA utils (a CPython extension) at runtime —
#     it needs a C compiler and Python headers or TrOCR generation dies with
#     "Failed to find C compiler" on the GPU path;
#   * microsoft/trocr-base-handwritten ships only a slow tokenizer;
#     transformers needs sentencepiece to convert it;
#   * transformers 5.x dropped that slow->fast conversion, which models
#     without tokenizer.json rely on; upstream htrflow targets the 4.x line.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        gcc libc6-dev python3.10-dev \
      && rm -rf /var/lib/apt/lists/* \
      && uv pip install --python /app/.venv/bin/python --no-cache \
           "sentencepiece==0.2.2" "transformers==4.57.6"; \
    fi

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
# Writable paths (HOME, TMPDIR, YOLO_CONFIG_DIR) are set by the Job spec
# into the tmpfs workdir; the root filesystem is mounted read-only.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
