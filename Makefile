.PHONY: install format lint check test ci build build-viewer scan publish \
        compose-up compose-test compose-down helm-lint docs-serve docs-build poc-push clean

# On RA hosts dagger containers need the corp CA; harmless elsewhere if the file exists.
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))

install:
	cd wrapper && uv sync --extra dev

format:
	cd wrapper && uvx ruff format .

lint:
	cd wrapper && uvx ruff check --fix .

check: format lint

test:
	cd wrapper && uv run --extra dev pytest -q

ci:
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

# PoC: build + push the wrapper image into the in-cluster k3s registry
poc-push:
	docker build -f .docker/htrflow-batch.dockerfile -t 127.0.0.1:30500/htrflow-batch:dev .
	docker push 127.0.0.1:30500/htrflow-batch:dev

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	rm -rf site/
