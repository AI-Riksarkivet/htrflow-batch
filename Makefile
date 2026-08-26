.PHONY: install format lint check test typecheck ci build build-viewer scan publish \
        compose-up compose-test compose-smoke compose-down helm-lint helm-template docs-serve docs-build \
        poc-push poc-push-arm64 build-wrapper build-reconciler scan-reconciler clean \
        warmup psa-labels \
        frontend-install frontend-test frontend-check frontend-build frontend-dev viewer-image

# Cluster-local constants (registry, S3 endpoint, bucket, namespace, release,
# NodePorts) live in a root `.env`; `.env.example` carries the PoC defaults
# and is loaded first so a missing `.env` changes nothing. Exported so
# `docker compose` interpolates the same values.
-include .env.example
-include .env
export HTR_RELEASE HTR_NAMESPACE HTR_REGISTRY HTR_REGISTRY_NODEPORT HTR_S3_ENDPOINT HTR_S3_NODEPORT \
       HTR_BUCKET HTR_VIEWER_NODEPORT HTR_DATA_PVC HTR_DEV_S3_ACCESS_KEY HTR_DEV_S3_SECRET_KEY

# On RA hosts dagger containers need the corp CA; harmless elsewhere if the file exists.
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))

# Local checkout of the Riksarkivet universalviewer4 fork, already built
# (`npm install && npm run build` → dist/). It is the docker build context for
# the viewer image; `viewer-image` stages the SPA into it.
UV4_DIR ?= $(HOME)/universalviewer4

CHART := charts/htrflow-batch
IMAGE_TAG ?= dev
ARCH := $(shell uname -m)

# uv workspace: always --all-packages. A plain `uv sync` prunes the shared
# venv back to the virtual root + dev group and drops the workspace members.
install:
	uv sync --all-packages

format:
	uvx ruff format packages scripts

lint:
	uvx ruff check --fix packages scripts

check: format lint

# Root invocation: the root pyproject's testpaths cover both packages, and
# --all-packages re-syncs the shared venv if a plain `uv sync` pruned it.
test:
	uv run --all-packages pytest -q

# `uvx ty check` cannot resolve workspace imports on its own — point each
# member at the shared workspace venv. CURDIR, not PWD: PWD is the shell's
# inherited cwd and stays wrong under `make -C`.
typecheck:
	cd packages/wrapper && uvx ty check src --python $(CURDIR)/.venv
	cd packages/reconciler && uvx ty check src --python $(CURDIR)/.venv

ci: typecheck
	dagger call checks $(DAGGER_CA)
	dagger call test $(DAGGER_CA)

build:
	dagger call build

build-viewer:
	dagger call build-viewer $(DAGGER_CA)

scan:
	dagger call scan-json $(DAGGER_CA)

# Publishing is manual and requires DOCKERHUB_USERNAME/DOCKERHUB_TOKEN env vars.
publish:
	dagger call publish-docker --component wrapper \
	  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN $(DAGGER_CA)

compose-up:
	cd .docker && docker compose up -d

# NOTE: requires the viewer image to be registry-pullable; on hosts where it is only tagged locally, use compose-smoke instead.
compose-test:
	dagger call compose-test

compose-smoke:
	cd .docker && docker compose up --build --abort-on-container-exit --exit-code-from wrapper wrapper && \
	docker compose up -d viewer && \
	curl -fsS -o /dev/null http://localhost:8080/uv.html && \
	docker compose down -v

compose-down:
	cd .docker && docker compose down -v

# Chart: lint + render on defaults and on ci/full-values.yaml (every feature
# on, no cluster lookups), then kubeconform when it is installed.
helm-lint:
	helm lint $(CHART)
	helm lint $(CHART) -f $(CHART)/ci/full-values.yaml

helm-template: helm-lint
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) > /dev/null
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml > /dev/null
	@if command -v kubeconform >/dev/null; then \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary; \
	else echo "kubeconform not installed — schema validation skipped"; fi

docs-serve:
	uvx zensical serve

docs-build:
	uvx zensical build --clean

# PoC: build + push the images into the in-cluster k3s registry ($(HTR_REGISTRY),
# from .env). Real registries go through `make publish` (dagger), which tests
# before it pushes. On an arm64 host the wrapper is built from the native GPU
# recipe (.docker/htrflow-batch-gpu-arm64.dockerfile, needs the local
# htrflow:v0.2.6-arm64 base — see that file) instead of the amd64 upstream
# image, which only runs under qemu and cannot reach the GPU (audit O13).
# Each push prints the digest to pin in values (`reconciler.image`,
# `exampleJob.image`); the chart refuses tags unless devStack.allowTagImages.
ifeq ($(ARCH),aarch64)
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch-gpu-arm64.dockerfile
else
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch.dockerfile
endif
WRAPPER_IMAGE := $(HTR_REGISTRY)/htrflow-batch:$(IMAGE_TAG)
RECONCILER_IMAGE := $(HTR_REGISTRY)/htrflow-reconciler:$(IMAGE_TAG)

build-wrapper:
	docker build -f $(WRAPPER_DOCKERFILE) -t $(WRAPPER_IMAGE) .

build-reconciler:
	docker build -f .docker/htrflow-reconciler.dockerfile -t $(RECONCILER_IMAGE) .

poc-push: build-wrapper build-reconciler
	docker push $(WRAPPER_IMAGE)
	docker push $(RECONCILER_IMAGE)
	@echo "wrapper:    $$(docker inspect --format '{{index .RepoDigests 0}}' $(WRAPPER_IMAGE))"
	@echo "reconciler: $$(docker inspect --format '{{index .RepoDigests 0}}' $(RECONCILER_IMAGE))"

# Explicit native-arm64 wrapper build regardless of the host architecture
# (buildx with an arm64 builder, or the GB10 node itself).
poc-push-arm64:
	$(MAKE) poc-push WRAPPER_DOCKERFILE=.docker/htrflow-batch-gpu-arm64.dockerfile

# Vulnerability scan of the reconciler image (the wrapper goes through
# `make scan` / dagger). Trivy pinned; HIGH/CRITICAL with a fix fail the target.
TRIVY_IMAGE ?= aquasec/trivy:0.65.0
scan-reconciler: build-reconciler
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  -v trivy-cache:/root/.cache/trivy $(TRIVY_IMAGE) image \
	  --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(RECONCILER_IMAGE)

# Pod hardening (docs: development/security, D14).
# Warm one pipeline's model cache — the read-only, offline cache batch Jobs
# use. The reconciler does this itself for campaigns-repo pipelines; this is
# the manual path for `values.pipelines` / the example Job.
DATA_PVC ?= $(HTR_DATA_PVC)
warmup:
	@test -n "$(PIPELINE)" -a -n "$(IMAGE)" || (echo "usage: make warmup PIPELINE=<id> IMAGE=<ref>"; exit 2)
	uv run --package htrflow-reconciler python -m htrflow_reconciler.warmup \
	  --pipeline $(PIPELINE) --image $(IMAGE) --namespace $(HTR_NAMESPACE) --data-pvc $(DATA_PVC) \
	  | kubectl apply -f -

# Helm cannot label a namespace it did not create. The enforce level comes
# from the installed release's `security.psaEnforce` (baseline while the
# devStack git daemon runs as root, restricted otherwise); warn/audit are
# always restricted so the hardened pods stay provably restricted. Override
# with PSA_ENFORCE=… before the first install.
PSA_ENFORCE ?= $(shell helm get values $(HTR_RELEASE) -n $(HTR_NAMESPACE) --all -o json 2>/dev/null \
                 | jq -r '.security.psaEnforce // "baseline"' 2>/dev/null || echo baseline)
psa-labels:
	@echo "enforce=$(PSA_ENFORCE) (from release $(HTR_RELEASE)/$(HTR_NAMESPACE) security.psaEnforce)"
	kubectl label ns $(HTR_NAMESPACE) --overwrite \
	  pod-security.kubernetes.io/enforce=$(PSA_ENFORCE) \
	  pod-security.kubernetes.io/warn=restricted \
	  pod-security.kubernetes.io/audit=restricted

# Campaign browser (bun/SvelteKit). The CA bundle is what gets bun through the
# RA proxy; TLS verification stays on.
frontend-install:
	cd frontend && NODE_EXTRA_CA_CERTS=$(CA_BUNDLE) bun install

frontend-test:
	cd frontend && bun run test

frontend-check:
	cd frontend && bun run check

frontend-build:
	cd frontend && NODE_EXTRA_CA_CERTS=$(CA_BUNDLE) bun run build

frontend-dev:
	cd frontend && bun run dev

# Stage the SPA into the UV repo (the docker build context) and build the nginx
# image. Local tag only — publishing goes through `dagger call build-viewer`.
# `docker push` afterwards prints the digest to pin as `viewer.image`.
VIEWER_IMAGE := $(HTR_REGISTRY)/uv4:$(IMAGE_TAG)
viewer-image: frontend-build
	rm -rf $(UV4_DIR)/campaign-app && cp -r frontend/dist $(UV4_DIR)/campaign-app
	rm -f $(UV4_DIR)/campaign-app/status.sample.json   # dev fixture, not shipped
	docker build -f .docker/uv4-viewer.dockerfile -t $(VIEWER_IMAGE) $(UV4_DIR)

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf site/
