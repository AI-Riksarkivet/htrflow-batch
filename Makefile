.PHONY: install format lint check test typecheck ci build build-viewer scan publish \
        compose-up compose-test compose-smoke compose-down helm-lint docs-serve docs-build poc-push clean \
        frontend-install frontend-test frontend-check frontend-build frontend-dev

# On RA hosts dagger containers need the corp CA; harmless elsewhere if the file exists.
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))

# uv workspace: always --all-packages. A plain `uv sync` prunes the shared
# venv back to the virtual root + dev group and drops the workspace members.
install:
	uv sync --all-packages

format:
	uvx ruff format packages

lint:
	uvx ruff check --fix packages

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

helm-lint:
	helm lint charts/htrflow-batch

docs-serve:
	uvx zensical serve

docs-build:
	uvx zensical build --clean

# PoC: build + push the images into the in-cluster k3s registry. This target is
# hardwired to 127.0.0.1:30500 on purpose — real registries go through
# `make publish` (dagger), which tests before it pushes.
poc-push:
	docker build -f .docker/htrflow-batch.dockerfile -t 127.0.0.1:30500/htrflow-batch:dev .
	docker push 127.0.0.1:30500/htrflow-batch:dev
	docker build -f .docker/htrflow-reconciler.dockerfile -t 127.0.0.1:30500/htrflow-reconciler:dev .
	docker push 127.0.0.1:30500/htrflow-reconciler:dev

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
