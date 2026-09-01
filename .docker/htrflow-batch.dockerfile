# htrflow-batch: stock upstream image + torch with Blackwell (sm_120) kernels
# + the D16 streaming wrapper (DESIGN.md §5.1). Build context = repo root.
#
# Reproducibility (audit W8/S7): every input is pinned.
#   * base image and the uv binary are pinned by digest;
#   * torch/torchvision are pinned to the versions the floating cu128
#     `--upgrade` resolved to on 2026-08-26 (the upstream base ships torch
#     2.6.0/torchvision 0.21.0 cu12x; the cu128 index carries cp310 wheels
#     up to 2.9.1/0.24.1 — the 2.13.0/0.28.0 the arm64 GPU image reports come
#     from that image's own base lock, see htrflow-batch-gpu-arm64.dockerfile);
#   * the wrapper's dependencies come from the workspace lock (`uv export`
#     with hashes), not a free resolution at build time.
# Refreshing a pin: `docker buildx imagetools inspect <ref>` for digests;
# https://download.pytorch.org/whl/cu128/torch/ for torch versions.
#
# Build args:
#   HTRFLOW_BASE_REVISION  `git describe --tags --always --dirty` of the htrflow
#                          checkout the base was built from; stamped into the
#                          `se.riksarkivet.htrflow.base.revision` label so the
#                          image says which htrflow it really runs (manifest.json
#                          only knows the package version, "0.2.6"). Defaults to
#                          the upstream tag's own revision suffix.
FROM airiksarkivet/htrflow:v0.2.6-35f48a7@sha256:e56a87f7ad2b9d4fd87dcbed32bfa56cb0ba7cddfcca97ebf0045b77462695de
ARG HTRFLOW_BASE_REVISION=v0.2.6-35f48a7

# uv 0.12.6 (multi-arch index digest)
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv

# swap torch/torchvision for cu128 builds (Blackwell sm_120 kernels)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      --index-url https://download.pytorch.org/whl/cu128 \
      "torch==2.9.1" "torchvision==0.24.1"

# The wrapper is a uv workspace member (packages/wrapper). It is installed
# with `uv pip install` into the base image's existing /app/.venv rather than
# with the workspace `uv sync` (ra-skills dockerfile/references/python-uv.md):
# that venv already carries htrflow and the cu128 torch installed above, and
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
    --mount=type=bind,source=packages/api/pyproject.toml,target=/opt/workspace/packages/api/pyproject.toml \
    cd /opt/workspace \
    && uv export --locked --package htrflow-batch-wrapper --no-dev --no-emit-project \
         -o /tmp/wrapper-requirements.txt \
    && uv pip install --python /app/.venv/bin/python --no-cache --require-hashes \
         -r /tmp/wrapper-requirements.txt \
    && rm /tmp/wrapper-requirements.txt
COPY packages/wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache --no-deps /opt/wrapper

LABEL org.opencontainers.image.base.name="docker.io/airiksarkivet/htrflow:v0.2.6-35f48a7" \
      se.riksarkivet.htrflow.base.revision="${HTRFLOW_BASE_REVISION}"

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
# Writable paths (HOME, TMPDIR, YOLO_CONFIG_DIR) are set by the Job spec
# into the tmpfs workdir; the root filesystem is mounted read-only.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
