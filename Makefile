.PHONY: install format lint check test typecheck test-driver-real ci build scan publish \
        compose-up compose-test compose-smoke compose-down helm-lint helm-template \
        install-devstack install-kyverno \
        docs-serve docs-build \
        poc-push poc-push-arm64 build-wrapper build-htrflow-base-arm64 build-web scan-web clean \
        campaigns-apply psa-labels e2e \
        frontend-install frontend-test frontend-check frontend-build frontend-dev

# Cluster-local constants (registry, S3 endpoint, bucket, namespace, release,
# NodePorts) live in a root `.env`; `.env.example` carries the PoC defaults
# and is loaded first so a missing `.env` changes nothing. Exported so
# `docker compose` interpolates the same values.
-include .env.example
-include .env
export HTR_RELEASE HTR_NAMESPACE HTR_REGISTRY HTR_REGISTRY_NODEPORT HTR_S3_ENDPOINT HTR_S3_NODEPORT \
       HTR_BUCKET HTR_WEB_NODEPORT HTR_DEV_S3_ACCESS_KEY HTR_DEV_S3_SECRET_KEY HTRFLOW_DIR

# On RA hosts dagger containers need the corp CA; harmless elsewhere if the file exists.
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))

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
	uv run --no-sync ty check packages/wrapper/src packages/converter/src packages/web/src

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
	dagger call build-wrapper

scan:
	dagger call scan-json $(DAGGER_CA)

# Publishing is manual and requires DOCKERHUB_USERNAME/DOCKERHUB_TOKEN env vars.
publish:
	dagger call publish-docker --component wrapper \
	  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN $(DAGGER_CA)

compose-up:
	cd .docker && docker compose up -d

# NOTE: requires riksarkivet/htrflow-web:latest to be registry-pullable —
# the dagger compose module mounts only .docker/, so the web service cannot
# build from the repo root and is image-only. That image is not published
# yet, so on this branch use `make compose-smoke`, which builds and tags it
# locally first.
compose-test:
	dagger call compose-test

# The compose `web` service is image-only (see the note on compose-test), so
# build it from this branch and tag it under the name compose expects first.
compose-smoke:
	$(MAKE) build-web WEB_IMAGE=riksarkivet/htrflow-web:latest
	cd .docker && docker compose up --build --abort-on-container-exit --exit-code-from wrapper wrapper && \
	docker compose up -d web && \
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
	@curl -fsS http://localhost:$(HTR_WEB_NODEPORT)/api/v1/jobs

# Chart: lint + render on defaults and on ci/full-values.yaml (every feature
# on, no cluster lookups), then kubeconform when it is installed. The local
# twin of `dagger call check-chart` (.dagger/checks.go).
#
# The prod chart's "defaults" render needs three --set overrides no cluster
# is present to `lookup`: the web front is always rendered (no enabled flag)
# and requires publicResultsBase + network.apiServer.cidr + a digest-pinned
# web.image. CHART_DEFAULT_SETS mirrors ci/full-values.yaml's shape with a
# placeholder digest/CIDR — never install with these.
DEVSTACK_CHART := charts/htrflow-devstack
CHART_DEFAULT_SETS := --set publicResultsBase=https://x/ \
                       --set network.apiServer.cidr=10.16.51.10/32 \
                       --set web.image=docker.io/riksarkivet/htrflow-web@sha256:0000000000000000000000000000000000000000000000000000000000000000
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
	@# RustFS on credentials nobody chose must be refused (B63 Task 27).
	@! helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) --set rustfs.enabled=true > /dev/null 2>&1 \
	  || { echo "devstack rendered RustFS with no credentials: the devStack.insecureDefaults guard is gone"; exit 1; }
	@if command -v kubeconform >/dev/null; then \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_DEFAULT_SETS) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) -f $(DEVSTACK_CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary; \
	else echo "kubeconform not installed — schema validation skipped"; fi

# PoC-only support infrastructure (RustFS, registry, nvidia device plugin)
# — its own chart, own release, same namespace as $(HTR_RELEASE)
# (charts/htrflow-devstack/README.md, "Installing"). Not for production.
# NVIDIA_DEVICE_PLUGIN=false renders the chart without the RuntimeClass and
# device-plugin DaemonSet every GPU pod depends on -- doing that while GPU
# pods are running deleted both from under them once (a 2-minute outage,
# docs/development/e2e-indexed-jobs.md "A failed Helm install still owns
# what it applied"). The guard below refuses that unless FORCE=1.
NVIDIA_DEVICE_PLUGIN ?= true
# Kyverno is the enforcement point for the chart's `security.policies`
# (digest pins, the image allow-list, model revisions -- B63 Task 22). Its
# own namespace and its own release: it is a cluster-wide admission
# controller, not a piece of this platform, and making it a devstack
# subchart would tie every `helm upgrade` of the PoC to it.
KYVERNO ?= true
KYVERNO_CHART_VERSION ?= 3.9.0

install-kyverno:
	helm upgrade --install kyverno oci://ghcr.io/kyverno/charts/kyverno \
	  -n kyverno --create-namespace --version $(KYVERNO_CHART_VERSION) --wait

install-devstack:
	@if [ "$(NVIDIA_DEVICE_PLUGIN)" = "false" ] && [ "$(FORCE)" != "1" ] && \
	  kubectl get pods -A -o json | jq -e '[.items[] | select(.metadata.namespace!="kube-system") | select(.status.phase=="Running" or .status.phase=="Pending") | select(.spec.runtimeClassName=="nvidia" or any(.spec.containers[]?; (.resources.requests["nvidia.com/gpu"]? // .resources.limits["nvidia.com/gpu"]?) != null))] | length > 0' >/dev/null; then \
	  echo "install-devstack: refusing NVIDIA_DEVICE_PLUGIN=false -- GPU pods are running and depend on the RuntimeClass/DaemonSet this would delete; set FORCE=1 to override."; exit 1; \
	fi
	@if [ "$(KYVERNO)" = "true" ]; then $(MAKE) install-kyverno; fi
	helm upgrade --install $(HTR_RELEASE)-devstack charts/htrflow-devstack -n $(HTR_NAMESPACE) --create-namespace \
	  --set rustfs.enabled=true --set registry.enabled=true \
	  --set nvidiaDevicePlugin.enabled=$(NVIDIA_DEVICE_PLUGIN) \
	  --set devStack.insecureDefaults=true

docs-serve:
	uvx zensical serve

docs-build:
	uvx zensical build --clean

# PoC: build + push the images into the in-cluster k3s registry ($(HTR_REGISTRY),
# from .env). Real registries go through `make publish` (dagger), which tests
# before it pushes. One wrapper dockerfile serves both architectures and
# `docker build` picks the base stage for the host it runs on — never
# `--platform`: the amd64 image only runs under qemu on this node, cannot
# reach the GPU (audit O13), and uv segfaults under the emulator.
# Each push prints the digest to pin in values (`web.image`, or a campaign
# pipeline's image); the chart refuses tags unless devStack.allowTagImages.
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch.dockerfile
WRAPPER_IMAGE := $(HTR_REGISTRY)/htrflow-batch:$(IMAGE_TAG)
WEB_IMAGE := $(HTR_REGISTRY)/htrflow-web:$(IMAGE_TAG)
# Provenance label (audit W8): on arm64 the wrapper builds FROM a locally
# built htrflow base (the upstream image is amd64-only), so that base's
# `git describe` from the HTRFLOW_DIR checkout (.env) is stamped as
# se.riksarkivet.htrflow.base.revision. On amd64 the base is the pinned
# upstream tag and the dockerfile's own default already says so.
# Lazily expanded: git only runs when a wrapper build actually happens.
HTRFLOW_DIR ?= $(HOME)/htrflow
HTRFLOW_ARM64_BASE ?= htrflow:v0.2.6-arm64
ifeq ($(ARCH),aarch64)
HTRFLOW_BASE_REVISION = $(shell git -C $(HTRFLOW_DIR) describe --tags --always --dirty 2>/dev/null || echo unknown)
WRAPPER_BUILD_ARGS = --build-arg HTRFLOW_ARM64_BASE=$(HTRFLOW_ARM64_BASE) \
                     --build-arg HTRFLOW_BASE_REVISION=$(HTRFLOW_BASE_REVISION)
else
WRAPPER_BUILD_ARGS =
endif

build-wrapper:
	docker build -f $(WRAPPER_DOCKERFILE) $(WRAPPER_BUILD_ARGS) -t $(WRAPPER_IMAGE) .

# The arm64 base the wrapper builds on. Built from the HTRFLOW_DIR checkout,
# which this repo treats as read-only: htrflow's lockfile is gitignored
# there, so the target refuses to run rather than writing a uv.lock into
# someone else's working tree. CI does the same build in a throwaway clone
# pinned to HTRFLOW_ARM64_BASE_REF (.github/workflows/publish.yml).
build-htrflow-base-arm64:
	@test -f $(HTRFLOW_DIR)/uv.lock || { \
	  echo "no $(HTRFLOW_DIR)/uv.lock — run 'uv lock' in that checkout first (this target will not write into it)"; \
	  exit 1; }
	docker build -f $(HTRFLOW_DIR)/docker/htrflow.dockerfile -t $(HTRFLOW_ARM64_BASE) $(HTRFLOW_DIR)

# The web image builds the SPA and the Universal Viewer inside itself, so
# this needs no pre-built dist/ and no UV checkout. The corp CA is passed as
# the optional `ca` build secret when present (RA hosts intercept TLS; the
# git clone and the npm/bun installs need it).
DOCKER_SECRET_CA := $(shell test -f $(CA_BUNDLE) && echo --secret id=ca,src=$(CA_BUNDLE))
build-web:
	docker build -f .docker/htrflow-web.dockerfile $(DOCKER_SECRET_CA) -t $(WEB_IMAGE) .

poc-push: build-wrapper build-web
	docker push $(WRAPPER_IMAGE)
	docker push $(WEB_IMAGE)
	@echo "wrapper: $$(docker inspect --format '{{index .RepoDigests 0}}' $(WRAPPER_IMAGE))"
	@echo "web:     $$(docker inspect --format '{{index .RepoDigests 0}}' $(WEB_IMAGE))"

# Deprecated alias of `poc-push`, kept one release for muscle memory: there
# is no separate arm64 recipe any more, `poc-push` builds the host's arch.
poc-push-arm64:
	@echo 'note: poc-push-arm64 is deprecated - make poc-push already builds the host architecture'
	$(MAKE) poc-push

# Vulnerability scan of the web image (the wrapper goes through
# `make scan` / dagger). Trivy pinned; HIGH/CRITICAL with a fix fail the target.
TRIVY_IMAGE ?= aquasec/trivy:0.65.0
scan-web: build-web
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  -v trivy-cache:/root/.cache/trivy $(TRIVY_IMAGE) image \
	  --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(WEB_IMAGE)

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

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf site/
