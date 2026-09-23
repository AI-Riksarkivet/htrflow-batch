.PHONY: install format lint check test typecheck test-driver-real ci build scan publish release-notes \
        compose-up compose-test compose-smoke compose-smoke-run compose-down helm-lint helm-template \
        install-devstack install-kyverno \
        docs-serve docs-build config-reference api-contract \
        scan-image poc-push poc-push-arm64 build-wrapper lock-htrflow-base transformers-requirements build-web build-campaigns scan-web clean install-kueue \
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
# Releases go through publish.yml (both architectures, signed); this pushes
# one unsigned image for the host's architecture under the bare version tag,
# and publish-docker refuses when that tag already exists (finding 3069).
publish:
	dagger call publish-docker --component wrapper \
	  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN $(DAGGER_CA)

# What the next GitHub release will list: git-cliff over the commits since the
# last v* tag (cliff.toml). The release workflow puts .github/release-notes.md
# above the same list when a tag is pushed.
release-notes:
	uv run --only-group release git-cliff --unreleased --strip header

compose-up:
	cd .docker && docker compose up -d

# The web service runs the image pinned in .docker/docker-compose.yml (the
# dagger module mounts only .docker/, so it cannot build one); this checks
# that release, not the checkout. `make compose-smoke` is the checkout's.
compose-test:
	dagger call compose-test

# The compose stack against images of THIS checkout, built by the same
# recipes as the images that ship (build-wrapper, build-web) and run by name
# through HTR_WRAPPER_IMAGE / HTR_WEB_IMAGE -- the compose file's own defaults
# pinned the published web image, so this used to smoke the last release
# instead of the branch (finding 3104).
compose-smoke: build-wrapper build-web
	$(MAKE) compose-smoke-run

# The smoke on its own, against whatever WRAPPER_IMAGE and WEB_IMAGE name:
# the images compose-smoke just built, or a published release by digest.
# The stack comes down (volumes included) however the run ends.
compose-smoke-run:
	cd .docker && trap 'docker compose down -v' EXIT && \
	export HTR_WRAPPER_IMAGE=$(WRAPPER_IMAGE) HTR_WEB_IMAGE=$(WEB_IMAGE) && \
	docker compose up --no-build --abort-on-container-exit --exit-code-from wrapper wrapper && \
	docker compose up --no-build -d web && \
	curl -fsS --retry 15 --retry-delay 2 --retry-all-errors -o /dev/null http://localhost:8080/uv.html

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
# For that reason a PRUNE=1 whose render produced NO campaigns at all is
# refused; ALLOW_EMPTY=1 goes with it when retiring the last campaign really
# is the point.
campaigns-apply:
	@test -n "$(DIR)" || (echo "usage: make campaigns-apply DIR=<campaigns-repo-dir>"; exit 2)
	uv run htrflow-campaigns apply $(DIR) --out $(DIR)/rendered --namespace $(HTR_NAMESPACE) \
	  $(if $(PRUNE),--prune) $(if $(ALLOW_EMPTY),--allow-empty)

# The reproducible core of the Indexed Jobs E2E (docs/development/e2e-indexed-jobs.md):
# validate the campaigns repo, render + apply it, then block until every
# campaign Job reaches a terminal condition, printing completedIndexes as it
# goes. DIR is the campaigns repo; CAMPAIGN_TIMEOUT caps the wait (seconds).
# The wait is scripts/e2e-wait.sh, where it is tested: it fails on no Job, a
# Failed Job or a kubectl that cannot read the cluster (finding 3102).
# The failure-path steps (a 404 manifest, the pod deadline, pause/resume, prune)
# are campaigns and kubectl in the run log, not this target.
CAMPAIGN_TIMEOUT ?= 3600
e2e:
	@test -n "$(DIR)" || (echo "usage: make e2e DIR=<campaigns-repo-dir>"; exit 2)
	uv run htrflow-campaigns validate $(DIR)
	$(MAKE) campaigns-apply DIR=$(DIR)
	@sel=$$(uv run python -c "from htrflow_converter.render import CAMPAIGN_SELECTOR; print(CAMPAIGN_SELECTOR)") \
	  && scripts/e2e-wait.sh $(HTR_NAMESPACE) "$$sel" $(CAMPAIGN_TIMEOUT)
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
# The defaults also leave the Kyverno policies off, which the chart refuses
# unless the render says so (B80) -- CHART_NO_POLICY_SETS is the render
# that does not, and must fail.
CHART_NO_POLICY_SETS := --set publicResultsBase=https://x/ \
                       --set network.apiServer.cidr=192.0.2.10/32 \
                       --set network.iiifCidrs='{203.0.113.27/32}' \
                       --set network.web.allowPublicIngress=true \
                       --set web.image=docker.io/riksarkivet/htrflow-web@sha256:0000000000000000000000000000000000000000000000000000000000000000
CHART_DEFAULT_SETS := $(CHART_NO_POLICY_SETS) --set security.policies.allowDisabled=true
# The production profile is rendered like any other input: it is the file
# the deployment page tells operators to start from, so a change that stops
# it rendering has to fail here. Its site-specific values are the operator's,
# so the fixture supplies placeholders for them.
CHART_PROD_SETS := --set publicResultsBase=https://x/ \
                       --set network.apiServer.cidr=192.0.2.10/32 \
                       --set network.web.ingressCidrs='{198.51.100.0/24}' \
                       --set network.s3Cidrs='{192.0.2.128/25}' \
                       --set network.clusterCidrs='{10.244.0.0/16,10.96.0.0/12}' \
                       --set network.iiifCidrs='{203.0.113.27/32}' \
                       --set web.image=docker.io/riksarkivet/htrflow-web@sha256:0000000000000000000000000000000000000000000000000000000000000000
helm-lint:
	helm lint $(CHART) $(CHART_DEFAULT_SETS)
	helm lint $(CHART) -f $(CHART)/ci/full-values.yaml
	helm lint $(CHART) -f $(CHART)/values-prod.yaml $(CHART_PROD_SETS)
	helm lint $(DEVSTACK_CHART)
	helm lint $(DEVSTACK_CHART) -f $(DEVSTACK_CHART)/ci/full-values.yaml

helm-template: helm-lint
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_DEFAULT_SETS) > /dev/null
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml > /dev/null
	helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/values-prod.yaml $(CHART_PROD_SETS) > /dev/null
	helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) > /dev/null
	helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) -f $(DEVSTACK_CHART)/ci/full-values.yaml > /dev/null
	@# An install with the policies off and no allowDisabled must be refused
	@# (B80) -- by that guard: every other refusal also exits non-zero, so the
	@# exit code alone would pass with this guard deleted (finding 3103).
	@out=$$(helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_NO_POLICY_SETS) 2>&1) \
	  && { echo "chart rendered with the policies off and no security.policies.allowDisabled: the B80 guard is gone"; exit 1; }; \
	  printf '%s\n' "$$out" | grep -qF 'or set security.policies.allowDisabled=true to accept that' \
	  || { printf '%s\n' "$$out"; echo "chart refused the policies-off render, but not with the B80 guard's sentence"; exit 1; }
	@# RustFS on credentials nobody chose must be refused (B63 Task 27), same rule.
	@out=$$(helm template $(HTR_RELEASE) $(DEVSTACK_CHART) -n $(HTR_NAMESPACE) --set rustfs.enabled=true 2>&1) \
	  && { echo "devstack rendered RustFS with no credentials: the devStack.insecureDefaults guard is gone"; exit 1; }; \
	  printf '%s\n' "$$out" | grep -qF 'or set devStack.insecureDefaults: true to accept generated or known ones' \
	  || { printf '%s\n' "$$out"; echo "devstack refused RustFS with no credentials, but not with the insecureDefaults guard's sentence"; exit 1; }
	@if command -v kubeconform >/dev/null; then \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) $(CHART_DEFAULT_SETS) | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/ci/full-values.yaml | kubeconform -strict -ignore-missing-schemas -summary && \
	  helm template $(HTR_RELEASE) $(CHART) -n $(HTR_NAMESPACE) -f $(CHART)/values-prod.yaml $(CHART_PROD_SETS) | kubeconform -strict -ignore-missing-schemas -summary && \
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
# what it applied"). scripts/gpu-pods-guard.sh refuses that unless FORCE=1,
# and refuses too when it cannot read the cluster's pods (finding 3102).
NVIDIA_DEVICE_PLUGIN ?= true
# Kyverno is the enforcement point for the chart's `security.policies`
# (digest pins, the image allow-list, model revisions -- B63 Task 22). Its
# own namespace and its own release: it is a cluster-wide admission
# controller, not a piece of this platform, and making it a devstack
# subchart would tie every `helm upgrade` of the PoC to it.
KYVERNO ?= true
KYVERNO_CHART_VERSION ?= 3.9.0
# Kueue is a prerequisite the chart does not install (it renders the queue
# objects Kueue reconciles). Its official Helm chart, the same one a GitOps
# deployment renders, so a cluster installed either way upgrades the same
# way: the release manifests applied server-side fight Helm over the CRDs.
# The chart's version is the release's without the leading v.
KUEUE_VERSION ?= v0.19.5

install-kueue:
	helm upgrade --install kueue oci://registry.k8s.io/kueue/charts/kueue \
	  -n kueue-system --create-namespace --version $(KUEUE_VERSION:v%=%) --wait

install-kyverno:
	helm upgrade --install kyverno oci://ghcr.io/kyverno/charts/kyverno \
	  -n kyverno --create-namespace --version $(KYVERNO_CHART_VERSION) --wait

install-devstack:
	@if [ "$(NVIDIA_DEVICE_PLUGIN)" = "false" ] && [ "$(FORCE)" != "1" ]; then scripts/gpu-pods-guard.sh; fi
	@if [ "$(KYVERNO)" = "true" ]; then $(MAKE) install-kyverno; fi
	helm upgrade --install $(HTR_RELEASE)-devstack charts/htrflow-devstack -n $(HTR_NAMESPACE) --create-namespace \
	  --set rustfs.enabled=true --set registry.enabled=true \
	  --set nvidiaDevicePlugin.enabled=$(NVIDIA_DEVICE_PLUGIN) \
	  --set devStack.insecureDefaults=true

# Both go through scripts/docs-site.sh: the site is built from docs/ minus
# docs/features/ (the stories are the backlog's view, not site content).
docs-serve:
	scripts/docs-site.sh serve

docs-build: config-reference
	scripts/docs-site.sh build --clean

# frontend/src/lib/fixtures/api-contract.json is real read-API output, parsed
# by the frontend's own zod schemas in a vitest (2026-09-14 audit). The
# committed file must equal this output -- packages/web/tests/test_contract.py
# asserts it, so `make ci`'s pytest run is what catches a stale fixture.
api-contract:
	uv run --no-sync python scripts/api_contract.py

# docs/reference/configuration.md is generated from the three config models
# and the chart's values (B63 Task 27). The committed page must equal this
# output -- packages/converter/tests/test_chart_agreement.py asserts it.
config-reference:
	uv run --no-sync python scripts/config_reference.py

# PoC: build + push the images into the in-cluster k3s registry ($(HTR_REGISTRY),
# from .env). Real registries go through `make publish` (dagger), which tests
# before it pushes. One wrapper dockerfile serves both architectures, built
# for the host it runs on — never `--platform`: the amd64 image only runs
# under qemu on this node, cannot reach the GPU (audit O13), and uv
# segfaults under the emulator.
# Each push prints the digest to pin in values (`web.image`, or a campaign
# pipeline's image); the chart refuses tags unless devStack.allowTagImages.
WRAPPER_DOCKERFILE ?= .docker/htrflow-batch.dockerfile
WRAPPER_IMAGE := $(HTR_REGISTRY)/htrflow-batch:$(IMAGE_TAG)
WEB_IMAGE := $(HTR_REGISTRY)/htrflow-web:$(IMAGE_TAG)
CAMPAIGNS_IMAGE := $(HTR_REGISTRY)/htrflow-campaigns:$(IMAGE_TAG)
# The wrapper dockerfile builds its htrflow base itself, from htrflow at the
# commit it pins (HTRFLOW_REF there). HTRFLOW_SRC=<checkout> builds it from a
# local checkout instead (the dockerfile's `htrflow-src` stage) and stamps
# that checkout's `git describe` as se.riksarkivet.htrflow.base.revision
# (audit W8); the checkout's pyproject.toml must still be the one the
# committed lock was made for. Lazily expanded: git only runs when a wrapper
# build actually happens.
HTRFLOW_DIR ?= $(HOME)/htrflow
HTRFLOW_SRC ?=
WRAPPER_BUILD_ARGS = $(if $(HTRFLOW_SRC),--build-context htrflow-src=$(HTRFLOW_SRC) \
  --build-arg HTRFLOW_BASE_REVISION=$(shell git -C $(HTRFLOW_SRC) describe --tags --always --dirty 2>/dev/null || echo unknown))

# IMAGE_TAG is what the image will be called, so it is also what it reports
# as its version (the status page's header, the OCI label).
VERSION_BUILD_ARG = --build-arg HTRFLOW_BATCH_VERSION=$(IMAGE_TAG)
# Which transformers line the wrapper image carries (see the dockerfile):
# unset = the dockerfile's default; TRANSFORMERS_VERSION=5.9.0 builds the
# image for models saved by transformers 5.
WRAPPER_BUILD_ARGS += $(if $(TRANSFORMERS_VERSION),--build-arg TRANSFORMERS_VERSION=$(TRANSFORMERS_VERSION))

build-wrapper:
	docker build -f $(WRAPPER_DOCKERFILE) $(WRAPPER_BUILD_ARGS) $(VERSION_BUILD_ARG) -t $(WRAPPER_IMAGE) .

# The htrflow base's lock (.docker/htrflow-base/): htrflow's pyproject.toml
# at the commit the wrapper dockerfile pins, fetched from GitHub, plus this
# repository's overlay (torch per architecture), locked in a scratch
# directory from the committed lock so only what the change forces moves;
# UV_LOCK_ARGS=--upgrade moves everything. Run it after moving HTRFLOW_REF or
# editing the overlay, and review the diff: the image build refuses a
# pyproject.toml the lock was not made for. The debian-slim uv image, not the
# distroless one: uv probes the filesystem for a libc before it can resolve
# wheel tags.
HTRFLOW_BASE_LOCK_DIR := .docker/htrflow-base
UV_LOCK_IMAGE := ghcr.io/astral-sh/uv:0.12.6-debian-slim@sha256:9ac2caa67916b63d27595589abd0f0f10930974c885cd962ee30b71fbab42d9f
HTRFLOW_REF = $(shell sed -n 's/^ARG HTRFLOW_REF=//p' $(WRAPPER_DOCKERFILE))
lock-htrflow-base:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	curl -fsSL -o "$$tmp/pyproject.toml" \
	  https://raw.githubusercontent.com/AI-Riksarkivet/htrflow/$(HTRFLOW_REF)/pyproject.toml && \
	cat $(HTRFLOW_BASE_LOCK_DIR)/overlay.toml >> "$$tmp/pyproject.toml" && \
	cp $(HTRFLOW_BASE_LOCK_DIR)/uv.lock "$$tmp"/ && \
	docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v "$$tmp:/w" -w /w $(DOCKER_CA) \
	  $(UV_LOCK_IMAGE) uv lock $(UV_LOCK_ARGS) && \
	cp "$$tmp/pyproject.toml" "$$tmp/uv.lock" $(HTRFLOW_BASE_LOCK_DIR)/ && \
	git diff --stat -- $(HTRFLOW_BASE_LOCK_DIR)

# The transformers lines of the wrapper image (.docker/transformers/<major>.in)
# compiled into the pinned, hashed <major>.txt the dockerfile installs with
# --no-deps. Rerun after editing an .in file and commit both.
transformers-requirements:
	docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(CURDIR)/.docker/transformers:/w -w /w $(DOCKER_CA) \
	  $(UV_LOCK_IMAGE) sh -c 'for f in *.in; do \
	    uv pip compile --quiet --no-deps --generate-hashes --universal --python-version 3.10 \
	      --custom-compile-command "make transformers-requirements" -o "$${f%.in}.txt" "$$f" || exit 1; done'

# The build backend the images build their own packages with
# (.docker/build-constraints.in), compiled with its whole dependency closure
# into the pinned, hashed build-constraints.txt. The dockerfiles build with
# `uv build --require-hashes`, so a build requirement missing from it fails
# the image build. Universal: the wrapper builds on Python 3.10, web and
# campaigns on 3.13. Rerun after editing the .in file and commit both.
.PHONY: build-constraints
build-constraints:
	docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(CURDIR)/.docker:/w -w /w $(DOCKER_CA) \
	  $(UV_LOCK_IMAGE) uv pip compile --quiet --generate-hashes --universal --python-version 3.10 \
	    --custom-compile-command "make build-constraints" -o build-constraints.txt build-constraints.in

# The web image builds the SPA and the Universal Viewer inside itself, so
# this needs no pre-built dist/ and no UV checkout. The corp CA is passed as
# the optional `ca` build secret when present (RA hosts intercept TLS; the
# git clone and the npm/bun installs need it).
DOCKER_SECRET_CA := $(shell test -f $(CA_BUNDLE) && echo --secret id=ca,src=$(CA_BUNDLE))
build-web:
	docker build -f .docker/htrflow-web.dockerfile $(DOCKER_SECRET_CA) $(VERSION_BUILD_ARG) -t $(WEB_IMAGE) .

# The converter as the Argo CD hook runs it (docs/reference/campaign-yaml.md,
# "With Argo CD"): distroless, uv-locked, no CA secret needed -- the recipe
# clones nothing (same as htrflow-web's own venv stage).
build-campaigns:
	docker build -f .docker/htrflow-campaigns.dockerfile $(VERSION_BUILD_ARG) -t $(CAMPAIGNS_IMAGE) .

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
# Same Trivy release and digest as .dagger/main.go, and the same VEX
# statements (.docker/distroless.openvex.json) as every dagger scan.
TRIVY_IMAGE ?= aquasec/trivy:0.65.0@sha256:a22415a38938a56c379387a8163fcb0ce38b10ace73e593475d3658d578b2436
scan-web: build-web
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  -v trivy-cache:/root/.cache/trivy -v $(CURDIR):/out:ro $(TRIVY_IMAGE) image \
	  --vex /out/.docker/distroless.openvex.json \
	  --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(WEB_IMAGE)

# Trivy over an image that exists only in the local docker daemon: the arm64
# wrapper, whose base the dagger engine cannot see (finding 3060). ci.yml
# and publish.yml gate on it with the defaults below (CRITICAL with a fix
# fails), security.yml also writes its SARIF report through it:
#   make scan-image SCAN_IMAGE=<ref> SCAN_SEVERITY=CRITICAL,HIGH \
#     SCAN_FLAGS="--format sarif --output /out/trivy.sarif"
# /out is the working directory.
SCAN_IMAGE ?= $(WRAPPER_IMAGE)
SCAN_SEVERITY ?= CRITICAL
SCAN_FLAGS ?= --ignore-unfixed --exit-code 1
scan-image:
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  -v trivy-cache:/root/.cache/trivy -v $(CURDIR):/out $(DOCKER_CA) $(TRIVY_IMAGE) image \
	  --skip-version-check --vex /out/.docker/distroless.openvex.json \
	  --severity $(SCAN_SEVERITY) $(SCAN_FLAGS) $(SCAN_IMAGE)

# Helm cannot label a namespace it did not create. The enforce level comes
# from the installed release's `security.psaEnforce` (baseline by default;
# historically because charts/htrflow-devstack's git daemon ran as root —
# that daemon is gone as of B63, `psaEnforce` itself wasn't revisited here);
# warn/audit are always restricted so the hardened pods stay provably
# restricted. Override with PSA_ENFORCE=… before the first install. The
# script refuses a release it cannot read and a level that is not baseline
# or restricted, where this target once wrote an empty `enforce=` (finding
# 3102).
psa-labels:
	@PSA_ENFORCE="$(PSA_ENFORCE)" scripts/psa-labels.sh $(HTR_RELEASE) $(HTR_NAMESPACE)

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
