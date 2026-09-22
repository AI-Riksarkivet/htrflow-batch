# htrflow-campaigns: the converter, as the Argo CD PostSync hook runs it
# (docs: reference/campaign-yaml.md, "With Argo CD"). Distroless like the web
# image: no shell, no package manager; the hook's clone uses dulwich from the
# same venv, so there is no git binary either.
FROM debian:13-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS venv
RUN apt-get update && apt-get install -y --no-install-recommends python3.13 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/bin/python3.13
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=packages/web/pyproject.toml \
    uv sync --frozen --no-install-workspace --package htrflow-converter --extra hook --no-editable
COPY pyproject.toml uv.lock ./
COPY packages/wrapper/pyproject.toml packages/wrapper/pyproject.toml
COPY packages/web/pyproject.toml packages/web/pyproject.toml
COPY packages/converter packages/converter
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package htrflow-converter --extra hook --no-editable

FROM gcr.io/distroless/python3-debian13:nonroot@sha256:8ee214843129f43e2ebf5e0ca9f2e4e6d8292143d1b8a6787f169b5898578884
LABEL org.opencontainers.image.licenses="EUPL-1.2"
COPY --from=venv /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
# The release this image is published under: see htrflow-web.dockerfile's
# comment on the same lines for why it is both an env var and an OCI label.
ARG HTRFLOW_BATCH_VERSION=dev
ENV HTRFLOW_BATCH_VERSION=${HTRFLOW_BATCH_VERSION}
LABEL org.opencontainers.image.version="${HTRFLOW_BATCH_VERSION}"

# Pod Security restricted (D14): unprivileged user, numeric so it needs no
# passwd entry -- distroless has no useradd, and no shell to run one.
USER 1000:1000
ENTRYPOINT ["/app/.venv/bin/htrflow-campaigns"]
CMD ["--help"]
