# htrflow-batch: stock upstream image + torch with Blackwell (sm_120) kernels
# + the D16 streaming wrapper (DESIGN.md §5.1).
FROM airiksarkivet/htrflow:v0.2.6-35f48a7

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# swap torch/torchvision for cu128 builds (Blackwell sm_120 kernels)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      --index-url https://download.pytorch.org/whl/cu128 \
      --upgrade torch torchvision

# The wrapper is now a uv workspace member (packages/wrapper). It is still
# installed with `uv pip install` into the base image's existing /app/.venv
# rather than with the two-step workspace `uv sync` (ra-skills
# dockerfile/references/python-uv.md): that venv already carries htrflow and the
# cu128 torch installed above, and `uv sync` would prune it back to the
# lockfile's contents, removing exactly the packages this image exists for.
# packages/wrapper is self-contained (hatchling, no workspace deps), so copying
# it out of the workspace and pip-installing it resolves cleanly on its own.
COPY packages/wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache /opt/wrapper

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
# Writable paths (HOME, TMPDIR, YOLO_CONFIG_DIR) are set by the Job spec
# into the tmpfs workdir; the root filesystem is mounted read-only.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
