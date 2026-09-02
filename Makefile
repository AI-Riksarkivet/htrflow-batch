.PHONY: install format lint check test typecheck test-driver-real ci build build-viewer scan publish \
        compose-up compose-test compose-smoke compose-down helm-lint helm-template install-devstack \
        docs-serve docs-build \
        poc-push poc-push-arm64 build-wrapper build-api scan-api clean \
        campaigns-apply psa-labels e2e \
        frontend-install frontend-test frontend-check frontend-build frontend-dev viewer-image

# Cluster-local constants (registry, S3 endpoint, bucket, namespace, release,
# NodePorts) live in a root `.env`; `.env.example` carries the PoC defaults
# and is loaded first so a missing `.env` changes nothing. Exported so
# `docker compose` interpolates the same values.
-include .env.example
-include .env
export HTR_RELEASE HTR_NAMESPACE HTR_REGISTRY HTR_REGISTRY_NODEPORT HTR_S3_ENDPOINT HTR_S3_NODEPORT \
       HTR_BUCKET HTR_VIEWER_NODEPORT HTR_DEV_S3_ACCESS_KEY HTR_DEV_S3_SECRET_KEY HTRFLOW_DIR

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

# ruff/ty run from the workspace venv (`uv run --no-sync`), never `uvx`: uvx
# resolves the newest release on every host while uv.lock pins the versions
# CI checks with, and the two drifted (audit T1). Root config in pyproject.toml
# excludes docs/ (fenced code in plans is not source).
format:
	uv run --no-sync ruff format .

lint:
	uv run --no-sync ruff check --fix .

check: format lint

# Root invocation: the root pyproject's testpaths cover both packages, and
# --all-packages re-syncs the shared venv if a plain `uv sync` pruned it.
test:
	uv run --all-packages pytest -q

# ty from the workspace venv resolves the members' imports; the dagger
# `typecheck` function runs the same command in CI.
typecheck:
	uv run --no-sync ty check packages/wrapper/src packages/converter/src packages/api/src

# Level 0 htrflow API pin (audit T4): the real Pipeline.from_config / Export /
# auto_import contract on a one-page CPU fixture, inside the locally built
# wrapper image (`make build-wrapper`; IMAGE_TAG selects an existing tag).
# pytest is not in the image, so it is installed into the venv for the run
# (root: the venv is root-owned). `dagger call test-driver` is the CI twin.
# The corp CA is mounted when present (same rule as DAGGER_CA) so the
# pytest install gets through an intercepting proxy.
PYTEST_VERSION = $(shell grep -A1 '^name = "pytest"$$' uv.lock | sed -n 's/^version = "\(.*\)"/\1/p')
DOCKER_CA := $(shell test -f $(CA_BUNDLE) && echo -v $(CA_BUNDLE):/etc/ssl/certs/corp-ca.crt:ro \
               -e SSL_CERT_FILE=/etc/ssl/certs/corp-ca.crt -e UV_SYSTEM_CERTS=true)
test-driver-real:
	docker run --rm --user 0 --entrypoint /bin/sh $(DOCKER_CA) \
	  -e CUDA_VISIBLE_DEVICES= -e HF_HUB_OFFLINE=1 \
	  -v $(CURDIR)/packages/wrapper/tests/test_driver_real.py:/driver-tests/test_driver_real.py:ro \
	  -w /tmp $(WRAPPER_IMAGE) -c \
	  'uv pip install --python /app/.venv/bin/python --no-cache "pytest==$(PYTEST_VERSION)" \
	   && /app/.venv/bin/python -m pytest -m htrflow -q -p no:cacheprovider \
	        -o "markers=htrflow: needs the htrflow runtime" /driver-tests'

# `make ci` = what .github/workflows/ci.yml runs: `checks` carries ruff, ty,
# the frontend (bun check/test/build) and the chart render; `test` is pytest.
# typecheck runs locally first as well: it is the fastest signal.
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

# Render a campaigns repo's pipelines/campaigns to Indexed Jobs and apply
# them to the cluster directly (no controller in the loop). DIR is the
# campaigns repo checkout (see examples/campaigns/). Everything this does —
# render order, the prune selector, the Kueue pause sync — lives in
# `htrflow-campaigns apply`, so CI, Argo CD and this target run one command
# and cannot drift apart; `--out` keeps the PoC's habit of committing
# `rendered/`.
#
# PRUNE=1 additionally deletes the objects a *previous* render left behind
# that this one no longer produces — what makes "deleting a campaign file
# cancels the campaign" true without Argo CD. It is opt-in on purpose:
# --prune deletes every converter-labelled object in the namespace that is
# not in THIS apply, so running it against a partial checkout (a probe
# directory with its own converter.yaml, say) would cancel everything else.
campaigns-apply:
	@test -n "$(DIR)" || (echo "usage: make campaigns-apply DIR=<campaigns-repo-dir>"; exit 2)
	uv run htrflow-campaigns apply $(DIR) --out $(DIR)/rendered $(if $(PRUNE),--prune)

# The reproducible core of the Indexed Jobs E2E (docs/development/e2e-indexed-jobs.md):
# validate the campaigns repo, render + apply it, then block until every
# campaign Job reaches a terminal condition, printing completedIndexes as it
# goes. DIR is the campaigns repo; CAMPAIGN_TIMEOUT caps the wait (seconds).
# The failure-path steps (a 404 manifest, MAX_SECONDS, pause/resume, prune)
# are campaigns and kubectl in the run log, not this target.
CAMPAIGN_TIMEOUT ?= 3600
e2e:
	@test -n "$(DIR)" || (echo "usage: make e2e DIR=<campaigns-repo-dir>"; exit 2)
	uv run htrflow-campaigns validate $(DIR)
	$(MAKE) campaigns-apply DIR=$(DIR)
	@kubectl -n $(HTR_NAMESPACE) wait --for=condition=complete --timeout=600s \
	  job -l "$$(uv run python -c "from htrflow_converter.render import CAMPAIGN_SELECTOR; print(CAMPAIGN_SELECTOR)"),app=htrflow-warmup"
	@sel=$$(uv run python -c "from htrflow_converter.render import CAMPAIGN_SELECTOR; print(CAMPAIGN_SELECTOR)"); \
	deadline=$$(( $$(date +%s) + $(CAMPAIGN_TIMEOUT) )); \
	while :; do \
	  pending=""; \
	  for j in $$(kubectl -n $(HTR_NAMESPACE) get job -l app=htrflow-batch,$$sel -o name); do \
	    done_idx=$$(kubectl -n $(HTR_NAMESPACE) get $$j -o jsonpath='{.status.completedIndexes}'); \
	    total=$$(kubectl -n $(HTR_NAMESPACE) get $$j -o jsonpath='{.spec.completions}'); \
	    cond=$$(kubectl -n $(HTR_NAMESPACE) get $$j -o jsonpath='{.status.conditions[?(@.status=="True")].type}'); \
	    echo "$$j completions=$$total completedIndexes=[$$done_idx] $$cond"; \
	    case "$$cond" in *Complete*|*Failed*) ;; *) pending="$$pending $$j" ;; esac; \
	  done; \
	  [ -z "$$pending" ] && break; \
	  [ $$(date +%s) -ge $$deadline ] && { echo "::error::still running:$$pending"; exit 1; }; \
	  sleep 15; \
	done
	@curl -fsS http://localhost:$(HTR_VIEWER_NODEPORT)/api/v1/jobs

# Chart: lint + render on defaults and on ci/full-values.yaml (every feature
# on, no cluster lookups), then kubeconform when it is installed. The local
# twin of `dagger call check-chart` (.dagger/checks.go).
#
# The prod chart's "defaults" render needs three --set overrides no cluster
# is present to `lookup`: the read API is always rendered (no enabled flag)
# and requires publicResultsBase + network.apiServer.cidr + a digest-pinned
# api.image. CHART_DEFAULT_SETS mirrors ci/full-values.yaml's shape with a
# placeholder digest/CIDR — never install with these.
DEVSTACK_CHART := charts/htrflow-devstack
CHART_DEFAULT_SETS := --set publicResultsBase=https://x/ \
                       --set network.apiServer.cidr=10.16.51.10/32 \
                       --set api.image=docker.io/riksarkivet/htrflow-api@sha256:0000000000000000000000000000000000000000000000000000000000000000
helm-lint:
	helm lint $(CHART) $(CHART_DEFAULT_SETS)
	helm lint $(CHART) -f $(CHART)/ci/full-values.yaml
	helm lint $(DEVSTACK_CHART)
	helm lint $(DEVSTACK_CHART) -f $(DEVSTACK_CHART)/ci/full-values.yaml

helm-template: helm-lint
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_DEFAULT_SETS) > /dev/null
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml > /dev/null
	helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) > /dev/null
	helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) -f $(DEVSTACK_CHART)/ci/full-values.yaml > /dev/null
	@if command -v kubeconform >/dev/null; then \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_DEFAULT_SETS) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) -f $(DEVSTACK_CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary; \
	else echo "kubeconform not installed — schema validation skipped"; fi

# PoC-only support infrastructure (RustFS, registry, nvidia device plugin)
# — its own chart, own release, same namespace as $(HTR_RELEASE)
# (charts/htrflow-devstack/README.md, "Installing"). Not for production.
install-devstack:
	helm upgrade --install $(HTR_RELEASE)-devstack charts/htrflow-devstack -n $(HTR_NAMESPACE) --create-namespace \
	  --set rustfs.enabled=true --set registry.enabled=true \
	  --set nvidiaDevicePlugin.enabled=true

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
# Each push prints the digest to pin in values (`api.image`, or a campaign
# pipeline's image); the chart refuses tags unless devStack.allowTagImages.
ifeq ($(ARCH),aarch64)
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch-gpu-arm64.dockerfile
else
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch.dockerfile
endif
WRAPPER_IMAGE := $(HTR_REGISTRY)/htrflow-batch:$(IMAGE_TAG)
API_IMAGE := $(HTR_REGISTRY)/htrflow-api:$(IMAGE_TAG)
# Provenance label (audit W8): the arm64 recipe builds FROM a locally built
# htrflow base, so the base's `git describe` from the HTRFLOW_DIR checkout
# (.env) is stamped as se.riksarkivet.htrflow.base.revision. The amd64
# recipe pulls the tagged upstream image and keeps the dockerfile default.
# Lazily expanded: git only runs when a wrapper build actually happens.
HTRFLOW_DIR ?= $(HOME)/htrflow
ifeq ($(WRAPPER_DOCKERFILE),.docker/htrflow-batch-gpu-arm64.dockerfile)
HTRFLOW_BASE_REVISION = $(shell git -C $(HTRFLOW_DIR) describe --tags --always --dirty 2>/dev/null || echo unknown)
WRAPPER_BUILD_ARGS = --build-arg HTRFLOW_BASE_REVISION=$(HTRFLOW_BASE_REVISION)
else
WRAPPER_BUILD_ARGS =
endif

build-wrapper:
	docker build -f $(WRAPPER_DOCKERFILE) $(WRAPPER_BUILD_ARGS) -t $(WRAPPER_IMAGE) .

build-api:
	docker build -f .docker/htrflow-api.dockerfile -t $(API_IMAGE) .

poc-push: build-wrapper build-api
	docker push $(WRAPPER_IMAGE)
	docker push $(API_IMAGE)
	@echo "wrapper: $$(docker inspect --format '{{index .RepoDigests 0}}' $(WRAPPER_IMAGE))"
	@echo "api:     $$(docker inspect --format '{{index .RepoDigests 0}}' $(API_IMAGE))"

# Explicit native-arm64 wrapper build regardless of the host architecture
# (buildx with an arm64 builder, or the GB10 node itself).
poc-push-arm64:
	$(MAKE) poc-push WRAPPER_DOCKERFILE=.docker/htrflow-batch-gpu-arm64.dockerfile HTRFLOW_DIR=$(HTRFLOW_DIR)

# Vulnerability scan of the read API image (the wrapper goes through
# `make scan` / dagger). Trivy pinned; HIGH/CRITICAL with a fix fail the target.
TRIVY_IMAGE ?= aquasec/trivy:0.65.0
scan-api: build-api
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  -v trivy-cache:/root/.cache/trivy $(TRIVY_IMAGE) image \
	  --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(API_IMAGE)

# Helm cannot label a namespace it did not create. The enforce level comes
# from the installed release's `security.psaEnforce` (baseline by default;
# historically because charts/htrflow-devstack's git daemon ran as root —
# that daemon is gone as of B63, `psaEnforce` itself wasn't revisited here);
# warn/audit are always restricted so the hardened pods stay provably
# restricted. Override with PSA_ENFORCE=… before the first install.
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
