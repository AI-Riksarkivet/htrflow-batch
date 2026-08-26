# Native arm64 GPU variant of .docker/htrflow-batch.dockerfile for the GB10
# node: the wrapper on top of a locally built arm64 htrflow base instead of
# the amd64-only airiksarkivet/htrflow — so CUDA is reachable natively, no
# qemu. Build context = repo root.
#
# The base is built locally from the ~/htrflow checkout (`uv lock` first —
# the lockfile is gitignored there), then:
#   docker build -f docker/htrflow.dockerfile -t htrflow:v0.2.6-arm64 ~/htrflow
#   docker build -f .docker/htrflow-batch-gpu-arm64.dockerfile \
#     --build-arg HTRFLOW_BASE_REVISION="$(git -C ~/htrflow describe --tags --always --dirty)" \
#     -t 127.0.0.1:30500/htrflow-batch:<tag> .
# Three extras beyond the amd64 recipe are required (see comments below):
# gcc + Python headers for triton, sentencepiece, and transformers 4.x.
#
# Reproducibility (audit W8/S7): the uv binary is digest-pinned, every pip
# install is version-pinned to what the live image (`htrflow-batch:live-v2`,
# 2026-08-26) reports, and the wrapper's dependencies come from the workspace
# lock. The base itself is a local tag with no registry digest: the
# HTRFLOW_BASE_REVISION build arg (above) is how the image records which
# htrflow commit it really runs — manifest.json only knows the package
# version "0.2.6", while the base has been built well past that tag. apt
# packages stay unpinned: Ubuntu's archive drops superseded versions, so an
# exact apt pin breaks the build on the next security update (a snapshot
# mirror is the real fix, out of scope here).
#
# torch: the base's own lock resolves torch/torchvision from PyPI, whose
# aarch64 wheels bundle CUDA 13 (torch reports 2.13.0+cu130 and runs on the
# GB10). The former "cu128 swap" step was a no-op on this arch — the cu128
# index carries no cp310 wheel newer than 2.9.1 — and is replaced by an
# explicit pin that fails the build if the base's lock drifts.
FROM htrflow:v0.2.6-arm64
ARG HTRFLOW_BASE_REVISION=unknown

# uv 0.12.6 (multi-arch index digest)
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# torch/torchvision as shipped by the base lock (PyPI aarch64, CUDA 13)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      "torch==2.13.0" "torchvision==0.28.0"

# Wrapper dependencies from the workspace lock (pinned + hashed), the
# package itself with --no-deps; see htrflow-batch.dockerfile for why this
# is `uv pip install` into the base venv and not a workspace `uv sync`.
# Bind-mount EVERY workspace member's pyproject.toml.
RUN --mount=type=bind,source=uv.lock,target=/opt/workspace/uv.lock \
    --mount=type=bind,source=pyproject.toml,target=/opt/workspace/pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=/opt/workspace/packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/reconciler/pyproject.toml,target=/opt/workspace/packages/reconciler/pyproject.toml \
    cd /opt/workspace \
    && uv export --locked --package htrflow-batch-wrapper --no-dev --no-emit-project \
         -o /tmp/wrapper-requirements.txt \
    && uv pip install --python /app/.venv/bin/python --no-cache --require-hashes \
         -r /tmp/wrapper-requirements.txt \
    && rm /tmp/wrapper-requirements.txt
COPY packages/wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache --no-deps /opt/wrapper

# triton JIT-compiles its CUDA utils (a CPython extension) at runtime — it
# needs a C compiler and Python headers or TrOCR generation dies with
# "Failed to find C compiler" on the GPU path.
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libc6-dev python3.10-dev \
    && rm -rf /var/lib/apt/lists/*

# microsoft/trocr-base-handwritten ships only a slow tokenizer; transformers
# needs sentencepiece to convert it.
RUN uv pip install --python /app/.venv/bin/python --no-cache "sentencepiece==0.2.2"

# transformers 5.x dropped the slow->fast tokenizer conversion that models
# without tokenizer.json (e.g. microsoft/trocr-base-handwritten) rely on;
# upstream htrflow targets the 4.x line.
RUN uv pip install --python /app/.venv/bin/python --no-cache "transformers==4.57.6"

LABEL org.opencontainers.image.base.name="htrflow:v0.2.6-arm64" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
