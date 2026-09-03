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
| `typecheck` | `ty check` on the wrapper, converter and web packages from the locked venv, what `make typecheck` runs locally |
| `test` | workspace pytest suite in a uv container (`uv run --no-sync pytest`, no GPU required) — wrapper, converter, web |
| `test-driver` | opt-in: `packages/wrapper/tests/test_driver.py` against the real htrflow inside the built wrapper image — the level-0 pin test ([Testing](testing.md)); `make test-driver-real` is the local twin |
| `build-wrapper` | production wrapper image from `.docker/htrflow-batch.dockerfile` — one file for both architectures, base stage selected by `TARGETARCH`. Builds for the engine's own platform; the optional `--platform` exists for a caller with an engine per platform and is never passed here, because a foreign platform means qemu and `uv` segfaults under it |
| `build-web` | the web image from `.docker/htrflow-web.dockerfile` (CPU-only, no torch): bun-builds the campaign browser SPA from `frontend/`, clones the Riksarkivet `universalviewer4` fork at the pinned `UV4_REF`, applies `.docker/uv4-uv-html.patch` and builds it with npm (UV's own toolchain), then puts both in the read API's `/app/static` — UV first, the SPA on top, so `/` is the SPA and `/uv.html` is UV. The corp CA goes in as the optional `ca` build secret |
| `scan` | Trivy scan of the built wrapper image (table output, exits non-zero on findings — not wired into `ci.yml`, since the CUDA/ubuntu base will never be alpine-clean) |
| `scan-web` | Trivy scan of the built web image (HIGH/CRITICAL, `--ignore-unfixed`) — a slim CPU-only base, so a clean gate is realistic; `make scan-web` is the local twin. It builds the image first, UV clone and npm build included, which is why `ci.yml` gates it the same way as the wrapper scan |
| `scan-json` | same as `scan` (the wrapper), JSON output, never fails the call |
| `publish-docker` | tests, builds, and pushes an image (`--component wrapper\|web`) to a registry; validates the tag against `packages/wrapper/pyproject.toml`'s version unless `--skip-validation`, then appends `--tag-suffix` (how `publish.yml` pushes `<tag>-amd64`) |
| `compose-up` | starts the `.docker/docker-compose.yml` stack as a dagger Service |
| `compose-test` | brings up the compose stack and curls the web service's `uv.html`. The module mounts only `.docker/` as the compose project, so the `web` service is image-only (`riksarkivet/htrflow-web:latest` must be pullable); `make compose-smoke` is the local path that builds and tags it from the branch first |

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
TLS-intercepting proxies where it is required (see the UV-build
`NODE_EXTRA_CA_CERTS` gotcha in the [test log](test-log.md)); the same
bundle gets bun through the RA proxy for `make frontend-install`.

## Makefile targets

`install`, `format`, `lint`, `check` (format + lint), `test`, `typecheck`,
`ci` (typecheck + dagger `checks` + `test`), `build`,
`build-wrapper`, `build-web`, `scan`, `scan-web`, `publish` (manual,
`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`), `compose-up`, `compose-test`,
`compose-smoke` (the verified local path — see [Testing](testing.md)),
`compose-down`, `helm-lint`, `helm-template` (lint + render both charts on
defaults and `ci/full-values.yaml` + kubeconform), `install-devstack`,
`docs-serve`, `docs-build` (`uvx zensical`), `poc-push` (build + push the
wrapper and web images into the in-cluster k3s registry, printing the
digests to pin; `poc-push-arm64` is a deprecated alias of it),
`build-htrflow-base-arm64` (the arm64 base the wrapper builds on, from
`HTRFLOW_DIR`), `campaigns-apply` (`htrflow-campaigns
apply`: render a campaigns repo, `kubectl apply` its `pipelines/` then
`campaigns/`, sync each campaign's pause),
`psa-labels`, `frontend-install/test/check/build/dev`, `clean`. The cluster-local constants they use come from `.env`
([Local k3s development](local-k3s.md)).

## Workflows

- **`ci.yml`** ("Tests") — on push to `main` and on pull requests: `dagger
  call checks`, `dagger call test`, and the `scripts/loc-budget.sh` line
  budgets (`SKIP_FRONTEND=1` until B63 Task 7 brings the frontend back under
  its 2 500-line budget); `dagger call scan-web` and `dagger call scan` (the
  wrapper) run on pushes to `main` and manual runs only, one job each so a
  failure in one still builds the other image. Both scans have to build
  their image first and both builds are expensive — the wrapper's ~10 GB CUDA base, and the web image's UV clone
  + npm build + bun build — so the pull-request path runs neither. A PR
  that changes a dockerfile gets its scan when it lands on `main`, before
  any image is published from it. A fourth job, `build-arm64`, runs on
  every trigger including pull requests: on an `ubuntu-24.04-arm` runner it
  builds the htrflow base from source at `HTRFLOW_ARM64_BASE_REF` and then
  the wrapper's `base-arm64` branch on top, pushing nothing — the arch the
  amd64 jobs cannot prove, in under three minutes.
  The dagger action is SHA-pinned and its engine `version` is pinned to
  `engineVersion` in `dagger.json`.
- **`publish.yml`** — manual (`workflow_dispatch`) only, one explicit tag
  per run. Three jobs: `publish` runs `dagger call publish-docker` (tests,
  builds, pushes) on `ubuntu-24.04` for the amd64 wrapper (`--tag-suffix
  -amd64`) and the web image; `publish-wrapper-arm64` builds the htrflow
  base from source and then the wrapper on an `ubuntu-24.04-arm` runner and
  pushes `<tag>-arm64`; `manifest` joins the two per-arch images into
  `riksarkivet/htrflow-batch:<tag>` with `docker buildx imagetools create`.
  Each job refuses to overwrite a published tag (its own and the manifest
  tag), and every pushed digest goes through the shared
  `.github/actions/sign-attest`: cosign (keyless, Sigstore OIDC) plus a
  SLSA build-provenance attestation, and for the two real images a
  Trivy-generated SPDX SBOM attestation as well. The cosign signature is
  what `security.verifyImages` in the chart verifies
  ([Chart Values](../reference/chart.md#trust-boundary-security)). The
  arm64 job is plain `docker build` rather than dagger because the dagger
  engine cannot see a base image that exists only in the runner's docker
  daemon; it runs `dagger call test` first so the gate is the same. This is
  stories B41/B42 done.
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
