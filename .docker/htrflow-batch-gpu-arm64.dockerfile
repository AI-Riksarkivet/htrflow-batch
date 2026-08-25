# Native arm64 GPU variant of .docker/htrflow-batch.dockerfile for the GB10
# node: same recipe as upstream (cu128 torch swap for Blackwell + wrapper),
# but FROM a locally built arm64 htrflow base instead of the amd64-only
# airiksarkivet/htrflow — so CUDA is reachable natively, no qemu.
#
# The base is built locally from the ~/htrflow checkout (`uv lock` first —
# the lockfile is gitignored there), then:
#   docker build -f docker/htrflow.dockerfile -t htrflow:v0.2.6-arm64 ~/htrflow
#   docker build -f .docker/htrflow-batch-gpu-arm64.dockerfile -t 127.0.0.1:30500/htrflow-batch:<tag> .
# Three extras beyond the amd64 recipe are required (see comments below):
# gcc + Python headers for triton, sentencepiece, and transformers<5.
FROM htrflow:v0.2.6-arm64

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# swap torch/torchvision for cu128 aarch64 builds (Blackwell kernels)
RUN uv pip install --python /app/.venv/bin/python --no-cache \
      --index-url https://download.pytorch.org/whl/cu128 \
      --upgrade torch torchvision

COPY packages/wrapper /opt/wrapper
RUN uv pip install --python /app/.venv/bin/python --no-cache /opt/wrapper

# triton JIT-compiles its CUDA utils (a CPython extension) at runtime — it
# needs a C compiler and Python headers or TrOCR generation dies with
# "Failed to find C compiler" on the GPU path.
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libc6-dev python3.10-dev \
    && rm -rf /var/lib/apt/lists/*

# microsoft/trocr-base-handwritten ships only a slow tokenizer; transformers
# needs sentencepiece to convert it.
RUN uv pip install --python /app/.venv/bin/python --no-cache sentencepiece

# transformers 5.x dropped the slow->fast tokenizer conversion that models
# without tokenizer.json (e.g. microsoft/trocr-base-handwritten) rely on;
# upstream htrflow targets the 4.x line.
RUN uv pip install --python /app/.venv/bin/python --no-cache "transformers<5"

# Pod Security restricted (D14): run as an unprivileged user. The Job spec
# pins runAsUser 1000 as well — both, so neither side can regress alone.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow
USER 1000:1000

ENTRYPOINT ["python", "-m", "htrflow_batch"]
