# htrflow-batch: the streaming wrapper (docs: how-it-works/wrapper) on top
# of htrflow, for BOTH architectures. Build context = repo root.
#
# One file, one recipe, no emulation. `uv` segfaults under `qemu-x86_64` and
# the GPU never crosses the emulation boundary anyway (a 2-page volume:
# ~55 s native vs 1 h+ emulated on CPU), so each architecture is built on a
# machine of its own and nothing here ever passes `--platform`.
#
# The htrflow base is built here, from source, for both architectures
# (findings 3060 and the amd64 half of 3104). It used to be two different
# things: on amd64 the published upstream image, whose htrflow is older than
# the API the driver drives (the level-0 pin test fails on it), and on arm64
# a separately built image that the dagger engine could not see. As stages
# of this file it is one recipe that every build path runs -- `make
# build-wrapper`, the dagger functions, the workflows -- from the same
# pinned inputs:
#
#   htrflow-src      AI-Riksarkivet/htrflow at HTRFLOW_REF, fetched by
#                    BuildKit. A local checkout replaces it with
#                    `--build-context htrflow-src=<dir>` (`make build-wrapper
#                    HTRFLOW_SRC=<dir>`).
#   htrflow-builder  the venv, `uv sync --locked` against
#                    .docker/htrflow-base/: htrflow's own pyproject.toml plus
#                    this repository's overlay (torch per architecture), and
#                    the lock both resolve to. htrflow does not commit a lock.
#   htrflow-base     the CUDA runtime image with that venv; the wrapper is
#                    installed on top of it below.
#
# Reproducibility (audit W8/S7, finding 3060): every input is pinned.
#   * images and the uv binary by digest, htrflow by commit;
#   * every Python package from a lockfile, with hashes: htrflow and torch
#     from the base lock (torch 2.9.1/torchvision 0.24.1 from PyTorch's
#     cu128 index on amd64, for Blackwell sm_120 kernels on CUDA 12 drivers;
#     2.13.0/0.28.0 from PyPI on arm64, CUDA 13), the wrapper's dependencies
#     and the leaf overrides from the workspace lock, the transformers line
#     from a hashed requirements file, and the build backend of the packages
#     built here (htrflow, the wrapper) from .docker/build-constraints.txt.
#     Nothing is resolved at build time;
#   * apt packages stay unpinned: Ubuntu's archive drops superseded
#     versions, so an exact apt pin breaks the build on the next security
#     update (a snapshot mirror is the real fix, out of scope here).
# Refreshing a pin: `docker buildx imagetools inspect <ref>` for digests;
# `make lock-htrflow-base` after moving HTRFLOW_REF or the overlay.
#
# Build args:
#   HTRFLOW_REF            the htrflow commit the base is built from. Renovate
#                          tracks it; a new one needs `make lock-htrflow-base`
#                          in the same change, or the build refuses it.
#   HTRFLOW_BASE_REVISION  what the image says it runs, stamped into the
#                          `se.riksarkivet.htrflow.base.revision` label and
#                          into every ALTO (manifest.json only knows the
#                          package version, "0.2.6"). Defaults to
#                          HTRFLOW_REF; `make build-wrapper HTRFLOW_SRC=…`
#                          passes the checkout's `git describe --dirty`.
ARG HTRFLOW_REF=0ede4da5493cf01ac97024558a5e4f468d5f360f

FROM scratch AS htrflow-src
ARG HTRFLOW_REF
ADD https://github.com/AI-Riksarkivet/htrflow.git#${HTRFLOW_REF} /

# nvidia/cuda:12.1.0-base-ubuntu22.04 (multi-arch index digest), the base the
# upstream htrflow image uses.
FROM nvidia/cuda:12.1.0-base-ubuntu22.04@sha256:40042016a816cbbe0504dd0a396e7cfc036a8aa43f5694af60dd6f8f87d24e52 AS htrflow-builder
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip python3-dev build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1
RUN uv venv --python 3.10
# The lock belongs to htrflow's pyproject.toml plus the overlay, so the
# pyproject.toml it is synced with must be exactly that: a checkout at any
# other commit is refused here instead of being installed against a lock
# made for different sources.
COPY .docker/htrflow-base/pyproject.toml .docker/htrflow-base/uv.lock /app/
COPY .docker/htrflow-base/overlay.toml /tmp/overlay.toml
COPY --from=htrflow-src pyproject.toml /tmp/htrflow-pyproject.toml
RUN cat /tmp/htrflow-pyproject.toml /tmp/overlay.toml | cmp -s - /app/pyproject.toml \
    || { echo "htrflow's pyproject.toml is not the one .docker/htrflow-base/uv.lock was made" \
              "for: run make lock-htrflow-base for this HTRFLOW_REF"; exit 1; }
# The dependencies come from the lock, hashed, as wheels: --no-build fails
# the step instead of building an sdist whose build requirements no lock
# pins.
RUN uv sync --locked --no-install-project --no-build
# htrflow itself is built here, and its build backend is not in uv.lock
# (a lock pins what is installed, not what builds it). `uv build` takes it
# from .docker/build-constraints.txt with --require-hashes, which refuses any
# build requirement that is not pinned and hashed there (audit 0923 D-11).
# A wheel, not an editable install: the image runs the installed package, so
# the source tree stays in this stage.
COPY --from=htrflow-src src/ /app/src/
COPY --from=htrflow-src LICENSE README.md /app/
RUN --mount=type=bind,source=.docker/build-constraints.txt,target=/tmp/build-constraints.txt \
    uv build --wheel --python /app/.venv/bin/python --require-hashes \
         --build-constraints /tmp/build-constraints.txt -o /tmp/dist . \
    && uv pip install --python /app/.venv/bin/python --no-deps /tmp/dist/*.whl \
    && rm -rf /tmp/dist

FROM nvidia/cuda:12.1.0-base-ubuntu22.04@sha256:40042016a816cbbe0504dd0a396e7cfc036a8aa43f5694af60dd6f8f87d24e52 AS htrflow-base
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 libgl1 libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=htrflow-builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app:"
ARG HTRFLOW_REF
ARG HTRFLOW_BASE_REVISION=${HTRFLOW_REF}
LABEL org.opencontainers.image.source.htrflow="https://github.com/AI-Riksarkivet/htrflow/tree/${HTRFLOW_REF}" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"
# Also as ENV: a label is invisible from inside the container, and the
# wrapper stamps this into every ALTO (provenance.py).
ENV HTRFLOW_BASE_REVISION=${HTRFLOW_BASE_REVISION}

FROM htrflow-base AS runtime
LABEL org.opencontainers.image.licenses="EUPL-1.2"

# uv 0.12.6 (multi-arch index digest)
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# The CUDA image's Ubuntu packages lag behind jammy-security (gnupg and
# openssl carry Trivy HIGH findings that Ubuntu has already fixed). Upgrade
# from the Ubuntu archive only: SourceParts=/dev/null hides
# /etc/apt/sources.list.d, where the NVIDIA CUDA repository lives, so no
# CUDA, driver or cuDNN package can move with it.
RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update -o Dir::Etc::SourceParts=/dev/null \
    && apt-get upgrade -y -o Dir::Etc::SourceParts=/dev/null \
    && rm -rf /var/lib/apt/lists/*

# The wrapper is a uv workspace member (packages/wrapper). It is installed
# with `uv pip install` into the base image's existing /app/.venv rather than
# with the workspace `uv sync` (ra-skills dockerfile/references/python-uv.md):
# that venv already carries htrflow and torch from the base lock, and
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
# The package itself is built with its build backend pinned and hashed, the
# same way as htrflow in the builder stage (audit 0923 D-11).
COPY packages/wrapper /opt/wrapper
RUN --mount=type=bind,source=.docker/build-constraints.txt,target=/tmp/build-constraints.txt \
    uv build --wheel --python /app/.venv/bin/python --no-cache --require-hashes \
         --build-constraints /tmp/build-constraints.txt -o /tmp/dist /opt/wrapper \
    && uv pip install --python /app/.venv/bin/python --no-cache --no-deps /tmp/dist/*.whl \
    && rm -rf /tmp/dist

# No compiler in this image, and no code generated at run time. torch 2.13
# (the arm64 build) routes some operators through its own Triton kernels
# (torch._native: TrOCR's attention bmm is one), and the first such call
# JIT-compiles Triton's CUDA launcher, a CPython extension, with the system C
# compiler. Without one TrOCR generation dies with "Failed to find C
# compiler"; with one the image carries gcc and the kernel headers for it
# (linux-libc-dev, a steady stream of kernel CVEs). TORCH_DISABLE_NATIVE_JIT
# keeps those operators on torch's precompiled ATen/cuBLAS kernels, the ones
# the amd64 build (torch 2.9) runs anyway, so both architectures execute the
# same kinds of kernels and nothing writes, compiles or loads new machine code
# under the read-only root filesystem. Nothing else here JIT-compiles:
# htrflow does not call torch.compile, and ultralytics leaves it off.
ENV TORCH_DISABLE_NATIVE_JIT=1

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
# Each line is a hashed requirements file, .docker/transformers/<major>.txt,
# compiled from the .in file beside it: transformers, the tokenizers and
# huggingface-hub that line needs, sentencepiece (arm64: TrOCR's slow tokenizer needs
# it to convert, and 5.x dropped that conversion) and protobuf (transformers
# only imports it on the error path of loading a slow tokenizer, and without
# it that path reports "requires the protobuf library" INSTEAD of the real
# error). They go in with --no-deps --require-hashes, so nothing here is
# resolved at build time and nothing else in the base moves; the check at
# the end of this file proves their own requirements are met. A
# TRANSFORMERS_VERSION the file does not pin fails the build.
ARG TRANSFORMERS_VERSION=4.57.6
RUN --mount=type=bind,source=.docker/transformers,target=/opt/transformers \
    req="/opt/transformers/${TRANSFORMERS_VERSION%%.*}.txt" \
    && { grep -q "^transformers==${TRANSFORMERS_VERSION} " "$req" \
         || { echo "TRANSFORMERS_VERSION=${TRANSFORMERS_VERSION} is not the version" \
                   ".docker/transformers/ pins for its line"; exit 1; }; } \
    && uv pip install --python /app/.venv/bin/python --no-cache --no-deps --require-hashes \
         -r "$req"

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
# the WRAPPER's own declared requirements are satisfied by what is installed,
# and so are those of the packages the transformers line installed without
# their dependencies.
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
for dist in ("htrflow-batch-wrapper", "transformers", "huggingface-hub"):
    for spec in requires(dist) or []:
        req = Requirement(spec)
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        try:
            have = version(req.name)
        except PackageNotFoundError:
            bad.append(f"{dist}: {spec}: not installed")
            continue
        if not req.specifier.contains(have, prereleases=True):
            bad.append(f"{dist}: {spec}: installed {have}")
if bad:
    sys.exit("requirements not satisfied:\n  " + "\n  ".join(bad))
print("wrapper and transformers requirements satisfied")
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
