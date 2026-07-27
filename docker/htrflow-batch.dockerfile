# htrflow-batch: stock upstream image + torch with Blackwell (sm_120) kernels.
# First real content of the derived image foreseen by DESIGN.md D2.
FROM airiksarkivet/htrflow:v0.2.6-35f48a7

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# swap torch/torchvision for cu128 builds (Blackwell sm_120 kernels)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      --index-url https://download.pytorch.org/whl/cu128 \
      --upgrade torch torchvision
