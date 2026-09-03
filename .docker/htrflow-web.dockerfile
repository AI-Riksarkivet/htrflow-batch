# The web front (B63 Task 17): one image with the read API, the campaign
# browser SPA and the Universal Viewer. Build context = repo root.
#
# Until Task 17 this was two images — this one and an nginx site proxying
# /api/ here. The nginx image, its ConfigMap and the proxy are gone:
# stages 1 and 2 build the two front ends, stage 3 is the Python service that
# serves them out of /app/static (packages/web, HTRFLOW_WEB_STATIC).
#
# Every FROM is tag+digest pinned (audit S7); Renovate tracks them, and the
# universalviewer4 commit below, from this file.
#
# RA hosts intercept TLS, so `git clone` and `npm/bun install` need the corp
# CA. Pass it as a build secret (`make build-web` does when the file exists):
#   docker build --secret id=ca,src=/etc/ssl/certs/ca-certificates.crt …
# The mount is optional — without it the stock CA set is used.

# ---- Stage 1: the campaign browser (SvelteKit, adapter-static -> dist/) ----
FROM oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4 AS spa
WORKDIR /app
COPY frontend/package.json frontend/bun.lock ./
# RA hosts sit behind a TLS-intercepting proxy; CI does not — only point bun
# at the mounted CA when the secret was actually passed (present, non-empty).
RUN --mount=type=secret,id=ca,target=/etc/ssl/certs/corp-ca.crt \
    if [ -s /etc/ssl/certs/corp-ca.crt ]; then export NODE_EXTRA_CA_CERTS=/etc/ssl/certs/corp-ca.crt; fi \
    && bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# ---- Stage 2: Universal Viewer (Riksarkivet fork + uv4-uv-html.patch) ----
# The ref is the exact commit the patch was generated against (branch
# "develop" @ f2e8f66, 2025-10-28): the patch was produced as an uncommitted
# `git diff` in a clone sitting on that commit, so `git apply` has no history
# to fall back on if upstream drifts. Bump deliberately, re-deriving the patch
# if needed. The patch enables the uv-iiif-config.json fetch (without it
# textRightPanelEnabled stays false and the ALTO panel never shows) and fixes
# the overlay coordinates — docs D19 notes.
FROM node:20-bookworm@sha256:8f693eaa7e0a8e71560c9a82b55fd54c2ae920a2ba5d2cde28bac7d1c01c9ba5 AS uv4
ARG UV4_REPO=https://github.com/Riksarkivet/universalviewer4
ARG UV4_REF=f2e8f66d3bd5a69e8e392764204d13d9524f63b2
WORKDIR /src
COPY .docker/uv4-uv-html.patch /tmp/uv4.patch
# RA hosts sit behind a TLS-intercepting proxy; CI does not — only export the
# CA env vars when the mounted secret was actually passed (present, non-empty).
RUN --mount=type=secret,id=ca,target=/etc/ssl/certs/corp-ca.crt \
    if [ -s /etc/ssl/certs/corp-ca.crt ]; then export NODE_EXTRA_CA_CERTS=/etc/ssl/certs/corp-ca.crt GIT_SSL_CAINFO=/etc/ssl/certs/corp-ca.crt; fi \
    && git init -q . \
    && git remote add origin "$UV4_REPO" \
    && git fetch -q --depth 1 origin "$UV4_REF" \
    && git checkout -q FETCH_HEAD \
    && git apply /tmp/uv4.patch \
    && npm install --no-audit --no-fund \
    && npm run build

# ---- Stage 3: the service (read API + the two builds above as its site) ----
# Workspace two-step sync per ra-skills dockerfile/references/python-uv.md:
# --frozen with only pyprojects bind-mounted (member sources absent), then
# --locked after COPY. Bind-mount EVERY workspace member's pyproject.toml.
# `apt-get upgrade` pulls the base's pending security fixes (Trivy CRITICAL
# gate in ci.yml).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/wrapper/pyproject.toml,target=packages/wrapper/pyproject.toml \
    --mount=type=bind,source=packages/converter/pyproject.toml,target=packages/converter/pyproject.toml \
    --mount=type=bind,source=packages/web/pyproject.toml,target=packages/web/pyproject.toml \
    uv sync --frozen --no-install-workspace --package htrflow-web --no-editable
COPY pyproject.toml uv.lock ./
COPY packages/wrapper/pyproject.toml packages/wrapper/pyproject.toml
COPY packages/converter/pyproject.toml packages/converter/pyproject.toml
COPY packages/web packages/web
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package htrflow-web --no-editable
# UV first, the SPA on top: the SPA's index.html deliberately replaces UV's
# demo one, so / is the campaign browser and UV keeps /uv.html. Same layering
# the nginx image used.
COPY --from=uv4 /src/dist/ /app/static/
COPY --from=spa /app/dist/ /app/static/
ENV PATH="/app/.venv/bin:$PATH" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    HTRFLOW_WEB_STATIC=/app/static
# Pod Security restricted (D14): unprivileged user.
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin htrflow-web
USER 1000:1000
EXPOSE 8081
CMD ["htrflow-web"]
