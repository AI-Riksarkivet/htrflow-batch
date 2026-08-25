# Reconciler: slim, CPU-only, no torch. Build context = repo root.
# Workspace two-step sync per ra-skills dockerfile/references/python-uv.md:
# --frozen with only pyprojects bind-mounted (member sources absent), then
# --locked after COPY. Bind-mount EVERY workspace member's pyproject.toml.
# TLS: only Debian's stock ca-certificates (public roots) is installed, which is
# all the in-cluster git clone of the campaigns repo needs to reach github.com.
# GIT_SSL_CAINFO/SSL_CERT_FILE point git and Python at that same bundle. No RA
# corporate root is baked in: on an RA-intercepted egress path the operator must
# mount the corp bundle over /etc/ssl/certs/ca-certificates.crt — a chart-level
# `extraCaSecret` value to do that is future work (spec §7.1).
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
# Pod Security restricted (D14): unprivileged user; the chart mounts an
# emptyDir at /tmp for the campaigns clone and sets HOME there.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin reconciler
USER 1000:1000
ENTRYPOINT ["python", "-m", "htrflow_reconciler"]
