# CI

CI logic lives in a dagger module (`.dagger/`, Go), not inline in YAML — the
GitHub Actions workflows are thin wrappers that call `dagger call <function>`.
That means the exact same commands run locally and in CI. `dagger functions`
lists what the module exposes on your checkout.

## Dagger functions

| Function | Does |
|---|---|
| `checks` | runs `lint`, `typecheck`, `check-frontend` and `check-chart` in that order; what `make ci` runs locally |
| `lint` | `ruff format --check` + `ruff check` on the workspace, from the locked venv (`uv run --no-sync`, never `uvx`) |
| `check-chart` | `helm lint` + a render, on the defaults and on each chart's `ci/full-values.yaml` (every optional template on), for both `charts/htrflow-batch` and `charts/htrflow-devstack`, then `kubeconform -strict` on every render; asserts the prod chart renders no `CronJob`, always renders the `htrflow-web` Deployment with a `/healthz` livenessProbe, and leaks no devstack-labelled object |
| `check-frontend` | `bun install --frozen-lockfile && bun run lint && bun run check && bun run test && bun run build` in an `oven/bun` container (CA bundle wired as for uv) |
| `typecheck` | `ty check` on the wrapper, converter and api packages from the locked venv, what `make typecheck` runs locally |
| `test` | workspace pytest suite in a uv container (`uv run --no-sync pytest`, no GPU required) — wrapper, converter, api |
| `test-driver` | opt-in: `packages/wrapper/tests/test_driver.py` against the real htrflow inside the built wrapper image — the level-0 pin test ([Testing](testing.md)); `make test-driver-real` is the local twin |
| `build` | production wrapper image from `.docker/htrflow-batch.dockerfile` |
| `build-web` | read API image from `.docker/htrflow-web.dockerfile` (CPU-only, no torch) |
| `build-viewer` | reproducible UV4 viewer image: clone the Riksarkivet `universalviewer4` fork at a pinned ref, apply `.docker/uv4-uv-html.patch`, build it with npm (UV's own toolchain), layer onto `nginxinc/nginx-unprivileged:1.27-alpine` — and bun-build the campaign browser SPA from `frontend/` on top, so `/` is the SPA and `/uv.html` is UV |
| `scan` | Trivy scan of the built wrapper image (table output, exits non-zero on findings — not wired into `ci.yml`, since the CUDA/ubuntu base will never be alpine-clean) |
| `scan-web` | Trivy scan of the built read API image (HIGH/CRITICAL, `--ignore-unfixed`) — CPU-only slim base, a clean gate is realistic here so `ci.yml` runs it on every push and PR; `make scan-web` is the local twin |
| `scan-json` | same as `scan` (the wrapper), JSON output, never fails the call |
| `publish-docker` | tests, builds, and pushes an image (`--component wrapper\|viewer\|api`) to a registry; validates the tag against `packages/wrapper/pyproject.toml`'s version unless `--skip-validation` |
| `compose-up` | starts the `.docker/docker-compose.yml` stack as a dagger Service |
| `compose-test` | brings up the compose stack and curls the viewer's `uv.html` — needs registry-pullable images |

The converter is not built by any dagger function — it is a pure Python
package, installed with `uvx --from
"git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter"
htrflow-campaigns` wherever it runs (a campaigns repo's own CI, or a laptop).

## The `--ca-bundle` flag

Every network-touching function accepts an optional `--ca-bundle <file>`
(wired through `withCaBundle` in `.dagger/main.go`), for TLS-interception on
locked-down corporate networks. The Makefile passes it automatically when a
CA bundle file exists:

```makefile
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))
```

**Not needed on dmlpai01** — probed directly: dagger's outbound calls
(PyPI/npm/HF Hub/Docker Hub/Trivy DB) resolve fine there without the bundle.
The wiring stays in place because it's cheap and other RA hosts do sit behind
TLS-intercepting proxies where it is required (see the viewer-build
`NODE_EXTRA_CA_CERTS` gotcha in the [test log](test-log.md)); the same
bundle gets bun through the RA proxy for `make frontend-install`.

## Makefile targets

`install`, `format`, `lint`, `check` (format + lint), `test`, `typecheck`,
`ci` (typecheck + dagger `checks` + `test`), `build`, `build-viewer`,
`build-wrapper`, `build-web`, `scan`, `scan-web`, `publish` (manual,
`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`), `compose-up`, `compose-test`,
`compose-smoke` (the verified local path — see [Testing](testing.md)),
`compose-down`, `helm-lint`, `helm-template` (lint + render both charts on
defaults and `ci/full-values.yaml` + kubeconform), `install-devstack`,
`docs-serve`, `docs-build` (`uvx zensical`), `poc-push` / `poc-push-arm64`
(build + push the wrapper and read API images into the in-cluster k3s
registry, printing the digests to pin), `campaigns-apply` (`htrflow-campaigns
apply`: render a campaigns repo, `kubectl apply` its `pipelines/` then
`campaigns/`, sync each campaign's pause),
`psa-labels`, `frontend-install/test/check/build/dev`, `viewer-image`,
`clean`. The cluster-local constants they use come from `.env`
([Local k3s development](local-k3s.md)).

## Workflows

- **`ci.yml`** ("Tests") — on push to `main` and on pull requests: `dagger
  call checks`, `dagger call test`, and the `scripts/loc-budget.sh` line
  budgets (`SKIP_FRONTEND=1` until B63 Task 7 brings the frontend back under
  its 2 500-line budget); a separate job runs `dagger call scan-web` on
  every push/PR and `dagger call scan` (the wrapper, ~10 GB CUDA base) on
  pushes to `main` and manual runs only. The dagger action is SHA-pinned and
  its engine `version` is pinned to `engineVersion` in `dagger.json`.
- **`publish.yml`** — manual (`workflow_dispatch`) only, one explicit tag
  per run, a matrix over the components (wrapper, api, viewer): it refuses
  to overwrite a published tag, runs `dagger call publish-docker` (tests,
  builds, pushes) with `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`, extracts the
  pushed digest, signs it with cosign (keyless, Sigstore OIDC), attaches a
  SLSA build-provenance attestation and a Trivy-generated SPDX SBOM
  attestation, and runs a Trivy scan that blocks on CRITICAL. The cosign
  signature is what `security.verifyImages` in the chart verifies
  ([Chart Values](../reference/chart.md#trust-boundary-security)). The
  arm64 GPU wrapper is *not* in the matrix — it is built locally with
  `make poc-push` ([Local k3s](local-k3s.md)); bringing it into CI is
  tracked as stories B41/B42.
- **`docs.yml`** ("Documentation") — manual only while the repo is private
  (GitHub Pages isn't available for private repos on the free plan, so a
  push trigger would fail every run): `pip install zensical` and `zensical
  build --clean`, then deploy to GitHub Pages. Restore the push trigger once
  the repo goes public.

## Dependency pins

Actions are SHA-pinned, `uv sync --locked` builds the images, the
dockerfiles pin base images and the uv binary by digest and torch by
version, and both charts pin every devStack image by digest. `renovate.json`
covers the image digests, actions, both lockfiles and the dagger engine
version; switching the updater to Dependabot is story B26.
