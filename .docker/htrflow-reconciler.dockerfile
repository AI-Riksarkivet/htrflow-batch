# Reconciler: slim, CPU-only, no torch. Build context = repo root.
# Workspace two-step sync per ra-skills dockerfile/references/python-uv.md:
# --frozen with only pyprojects bind-mounted (member sources absent), then
# --locked after COPY. Bind-mount EVERY workspace member's pyproject.toml.
# RA firewall CA is baked in so in-cluster git clone of the campaigns repo
# works through TLS interception (spec §7.1).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/reconciler/pyproject.toml,target=packages/reconciler/pyproject.toml \
    uv sync --frozen --no-install-workspace --package htrflow-reconciler --no-editable
COPY pyproject.toml uv.lock ./
COPY packages/wrapper/pyproject.toml packages/wrapper/pyproject.toml
COPY packages/reconciler packages/reconciler
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package htrflow-reconciler --no-editable
ENV PATH="/app/.venv/bin:$PATH" \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENTRYPOINT ["python", "-m", "htrflow_reconciler"]
