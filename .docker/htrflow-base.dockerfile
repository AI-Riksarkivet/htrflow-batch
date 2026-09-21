# The arm64 htrflow base the wrapper image builds FROM (its `base-arm64`
# stage). There is no published arm64 htrflow image, so this is built from a
# checkout of AI-Riksarkivet/htrflow, which is the build CONTEXT; this file
# and the lockfile are this repository's (audit finding 3060):
#
#   docker build -f .docker/htrflow-base.dockerfile \
#     --build-context lock=.docker/htrflow-base \
#     -t htrflow:v0.2.6-arm64 <htrflow checkout>
#
# (`make build-htrflow-base-arm64` and .github/actions/build-htrflow-base-arm64
# run exactly that.) It is htrflow's own docker/htrflow.dockerfile with every
# input pinned, because that file takes the CUDA image and uv by tag (uv by
# `latest`) and htrflow's uv.lock is gitignored, so each build used to lock
# afresh and a new upstream release went straight into a signed image:
#
#   * the CUDA base and uv by digest;
#   * the dependencies from the lockfile committed next to this file,
#     installed with `uv sync --locked`, which refuses a checkout whose
#     pyproject.toml no longer matches that lock (a moved
#     HTRFLOW_ARM64_BASE_REF) instead of re-resolving it. Refresh it with
#     `make lock-htrflow-base` and review the diff;
#   * apt packages stay unpinned, as in the wrapper dockerfile: Ubuntu's
#     archive drops superseded versions.
#
# Otherwise it matches the upstream file, so the venv at /app/.venv is the one
# the wrapper dockerfile expects.

# nvidia/cuda:12.1.0-base-ubuntu22.04 (multi-arch index digest)
FROM nvidia/cuda:12.1.0-base-ubuntu22.04@sha256:40042016a816cbbe0504dd0a396e7cfc036a8aa43f5694af60dd6f8f87d24e52 AS builder

ARG PYTHON_VERSION=3.10
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} \
    python3-pip \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# uv 0.12.6 (multi-arch index digest), the same binary the wrapper image uses
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1
ENV UV_NO_CACHE=1

RUN uv venv --python ${PYTHON_VERSION}

# Dependencies first (layer caching): the checkout's pyproject.toml, this
# repository's lock.
COPY pyproject.toml /app/
COPY --from=lock uv.lock /app/
RUN uv sync --locked --no-install-project

COPY src/ /app/src/
COPY LICENSE README.md /app/

RUN uv sync --locked

FROM nvidia/cuda:12.1.0-base-ubuntu22.04@sha256:40042016a816cbbe0504dd0a396e7cfc036a8aa43f5694af60dd6f8f87d24e52 AS runtime

ARG PYTHON_VERSION=3.10
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python${PYTHON_VERSION} \
    libgl1 \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"
