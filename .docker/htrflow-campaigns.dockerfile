# htrflow-campaigns: the converter, as the Argo CD PostSync hook runs it
# (docs: reference/campaign-yaml.md, "With Argo CD"). Distroless like the web
# image: no shell, no package manager; the hook's clone uses dulwich from the
# same venv, so there is no git binary either.
# The interpreter is uv's CPython build, as in the web image: see
# htrflow-web.dockerfile's stage 3 for why, and why it moves by hand.
FROM debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS venv
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON=3.13.16
RUN uv python install --no-bin \
    && rm -rf /opt/python/cpython-*/lib/python3.*/site-packages/pip \
         /opt/python/cpython-*/lib/python3.*/site-packages/pip-*.dist-info
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-managed
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=packages/web/pyproject.toml \
    uv sync --frozen --no-install-workspace --no-build --package htrflow-converter --extra hook
COPY pyproject.toml uv.lock ./
COPY packages/wrapper/pyproject.toml packages/wrapper/pyproject.toml
COPY packages/web/pyproject.toml packages/web/pyproject.toml
COPY packages/converter packages/converter
# The member itself: --locked proves the lock still matches the pyprojects
# and --no-build that every dependency came from it as a wheel; then the
# package is built with its build backend pinned and hashed
# (.docker/build-constraints.txt; --require-hashes refuses anything else,
# audit 0923 D-11) and installed as a wheel.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.docker/build-constraints.txt,target=/tmp/build-constraints.txt \
    uv sync --locked --no-install-workspace --no-build --package htrflow-converter --extra hook \
    && uv build --wheel --package htrflow-converter --require-hashes \
         --build-constraints /tmp/build-constraints.txt -o /tmp/dist \
    && uv pip install --python /app/.venv/bin/python --no-build --no-deps /tmp/dist/*.whl \
    && rm -rf /tmp/dist

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:3ee00291cf386ec52a552e6a858d555debd74776eb4bfa53fa3df2cd6677515e
LABEL org.opencontainers.image.licenses="EUPL-1.2"
COPY --from=venv /opt/python /opt/python
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
