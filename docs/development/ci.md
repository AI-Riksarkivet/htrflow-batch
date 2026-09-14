# CI

CI logic lives in a dagger module (`.dagger/`, Go), not inline in YAML: the
GitHub Actions workflows are thin wrappers that call `dagger call
<function>`, so the same commands run locally and in CI. `dagger functions`
lists what the module exposes on your checkout.

## Dagger functions

| Function | Does |
|---|---|
| `checks` | runs `lint`, `typecheck`, `check-frontend` and `check-chart` in that order; what `make ci` runs locally |
| `lint` | `ruff format --check` and `ruff check` on the workspace, from the locked venv (`uv run --no-sync`, never `uvx`, which resolves the newest release and drifts from `uv.lock`) |
| `typecheck` | `ty check` on the wrapper, converter and web packages from the locked venv; `make typecheck` is the local twin |
| `check-frontend` | `bun install --frozen-lockfile`, then `bun run check`, `bun run test` and `bun run build`, in a digest-pinned node container carrying the pinned bun binary (vitest needs a real node runtime) |
| `check-chart` | `helm lint` and a render of both charts on their defaults and on each chart's `ci/full-values.yaml`, plus a render the devstack chart must refuse (RustFS without chosen credentials); asserts the production chart renders no `CronJob`, always renders the `htrflow-web` Deployment with a `/healthz` livenessProbe, and renders no devstack-labelled object; then `kubeconform -strict` on every render and on the converter's Job and ConfigMap skeletons |
| `test` | the workspace pytest suite in a uv container (`uv run --no-sync pytest`, no GPU) — wrapper, converter, web |
| `test-driver` | opt-in: `packages/wrapper/tests/test_driver_real.py` against the real htrflow inside the built wrapper image — the level-0 pin test ([Testing](testing.md)); `make test-driver-real` is the local twin |
| `build-wrapper` | the wrapper image from `.docker/htrflow-batch.dockerfile`, for the engine's own platform. The optional `--platform` exists for a caller with an engine per platform; nothing here passes it ([Releasing](releasing.md#one-dockerfile-every-architecture)) |
| `build-web` | the web image from `.docker/htrflow-web.dockerfile` (CPU-only, no torch): the campaign browser SPA, the Universal Viewer fork at the pinned `UV4_REF` with `.docker/uv4-uv-html.patch` applied, and the read API that serves both. A CA bundle goes in as the optional `ca` build secret |
| `scan` | Trivy over the built wrapper image; table output, fails on findings (default `CRITICAL,HIGH`, unfixed findings ignored) |
| `scan-web` | the same over the built web image — a slim CPU-only base, so a clean gate is realistic; `make scan-web` is the local twin |
| `scan-json` | `scan` with JSON output that never fails the call; what `make scan` runs |
| `publish-docker` | tests, builds and pushes one image (`--component wrapper\|web`) and returns its reference with the digest ([Releasing](releasing.md#publishing)) |
| `compose-up` | starts the `web` service of the `.docker/docker-compose.yml` project as a dagger Service |
| `compose-test` | brings up the compose stack and fetches the web service's `/uv.html`. The module mounts only `.docker/` as the compose project, so the `web` service is image-only and must be pullable; `make compose-smoke` builds and tags it from the checkout first |

The converter is not built by any dagger function — it is a pure Python
package, installed with `uvx --from
"git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter"
htrflow-campaigns` wherever it runs (a campaigns repo's own CI, or a
workstation).

## What the containers see: exclude, not include

`lint`, `typecheck` and `test` build their container with `buildWithUv` in
`.dagger/main.go`, which mounts the **whole repository minus `repoExclude`**:
VCS and worktree plumbing, `.gitignore`'s entries, and dependency and build
caches. `check-frontend` does the same for the `frontend/` subtree with its
own short exclude list.

An allow-list is silently wrong by default: a new top-level path (a fixture,
a document a test reads, a new package) is invisible inside the container
until someone adds it, so a test can pass in CI only because its input was
never mounted. An exclude list inverts the failure: a new path is visible by
default, and a missing exclude is a harmless over-inclusion.
`packages/converter/tests/test_repo_visible_in_ci.py` pins this by reading
`docs/reference/campaign-yaml.md` inside the `test` container. Keep
`repoExclude` in step with `.gitignore` by hand; nothing parses one into the
other.

## The `--ca-bundle` flag

Every network-touching function accepts an optional `--ca-bundle <file>`
(`withCaBundle` in `.dagger/main.go`) for networks behind a TLS-inspecting
corporate proxy. It mounts the bundle and points `SSL_CERT_FILE` (Python,
uv) and `NODE_EXTRA_CA_CERTS` (node, npm, bun) at it. The Makefile passes
it whenever the bundle file exists, which is harmless on an open network:

```makefile
CA_BUNDLE ?= /etc/ssl/certs/ca-certificates.crt
DAGGER_CA := $(shell test -f $(CA_BUNDLE) && echo --ca-bundle $(CA_BUNDLE))
```

The same file reaches the other paths that download: `make build-web` passes
it as the `ca` build secret, `make test-driver-real` mounts it into the
container, and `make frontend-install` / `frontend-build` export it as
`NODE_EXTRA_CA_CERTS`. Node and npm do not read the system certificate
store, so behind such a proxy the Universal Viewer's `npm install` fails with
a misleading npm-internal crash rather than a certificate error unless
`NODE_EXTRA_CA_CERTS` is set — the web dockerfile sets it, and
`GIT_SSL_CAINFO` for the viewer clone, only when the secret was passed.

## Makefile targets

- **Workspace:** `install`, `format`, `lint`, `check` (format + lint),
  `test`, `typecheck`, `test-driver-real`, `ci` (typecheck, then dagger
  `checks` and `test`), `clean`.
- **Images:** `build` (`dagger call build-wrapper`), `build-wrapper` and
  `build-web` (plain `docker build`, tagged `$(HTR_REGISTRY)/…:$(IMAGE_TAG)`),
  `poc-push` (both builds, pushed to the dev registry, digests printed),
  the htrflow base built from a checkout
  ([Dev cluster](dev-cluster.md#the-gpu-wrapper-image)), `scan` (dagger
  `scan-json`), `scan-web` (Trivy, HIGH/CRITICAL with a fix fails),
  `publish` (manual, needs `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`).
- **Compose:** `compose-up`, `compose-test`, `compose-smoke` (the local
  path — see [Testing](testing.md)), `compose-down`.
- **Charts:** `helm-lint`, `helm-template` (lint, render both charts on
  defaults and `ci/full-values.yaml`, kubeconform).
- **Cluster:** `install-kueue` (the upstream release manifests at
  `KUEUE_VERSION`, applied server-side), `install-kyverno` (the admission
  controller `security.policies.enabled` needs, at
  `KYVERNO_CHART_VERSION`), `install-devstack`, `psa-labels`,
  `campaigns-apply` (render a campaigns repo, server-side apply its
  pipelines then its campaigns, sync each campaign's pause; `PRUNE=1` adds
  `--prune`), `e2e` (validate, apply, then block until every campaign Job
  reaches a terminal condition).
- **Documentation:** `docs-serve`, `docs-build` (through
  `scripts/docs-site.sh`), `config-reference` (regenerates
  `docs/reference/configuration.md`; a test asserts the committed page
  equals it).
- **Frontend:** `frontend-install`, `frontend-test`, `frontend-check`,
  `frontend-build`, `frontend-dev`.

The cluster constants these targets use come from `.env`
([Dev cluster](dev-cluster.md#env)).

## Workflows

- **`ci.yml`** ("Tests") — on push to `main`, on pull requests and by hand.
  The `ci` job runs `dagger call checks`, `dagger call test` and the
  `scripts/loc-budget.sh` line budgets. Two scan jobs, `scan-web` and
  `scan-wrapper`, run `scan-web` and `scan` with `--severity CRITICAL
  --ignore-unfixed` on pushes to `main` and manual runs only: each has to
  build its image first, and both builds are expensive (the wrapper's CUDA
  base; the web image's viewer clone and npm and bun builds). A pull request
  that changes a dockerfile gets its scan when it lands on `main`, before
  any image is published from it; one job per image so a failure in one
  still builds the other. A fourth job runs on every trigger, pull requests
  included, on a native runner of the second architecture the wrapper ships
  for: it builds the htrflow base from source at the pinned htrflow commit
  and the wrapper on top of it, pushes nothing, and prints the image's base
  labels — so both architectures of a dockerfile change are proven before
  it lands.
- **`publish.yml`** — manual, one explicit tag per run; tests, builds,
  pushes, signs and attests both images
  ([Releasing](releasing.md#the-publish-workflow)).
- **`docs.yml`** ("Documentation") — on push to `main` and by hand:
  `pip install zensical`, `scripts/docs-site.sh build --clean`, then deploy
  to GitHub Pages.

Every dagger step pins the `dagger-for-github` action by SHA and its engine
`version` to `engineVersion` in `dagger.json`.

## Dependency pins

Every input is pinned, and [Renovate](https://docs.renovatebot.com/) keeps
the pins current. `renovate.json` holds the whole policy; this is where
each kind of pin lives and how it moves.

| Pin | Lives in | Updated by |
|---|---|---|
| GitHub Actions | `uses:` lines, by commit SHA with the version as a comment | Renovate, one grouped weekly PR |
| Base and tool images | `FROM` lines in `.docker/*.dockerfile`, image values in both charts' `values.yaml`, `.docker/docker-compose.yml`, the image constants in `.dagger/main.go`, digest-pinned `run:` images in workflows, `TRIVY_IMAGE` in the `Makefile` — each as tag plus digest | Renovate, one grouped weekly PR (regex managers cover the dagger module, workflows and Makefile) |
| Python dependencies | `uv.lock` (workspace) | Renovate lockfile maintenance weekly; runtime minor and patch grouped, majors and dev tools in their own PRs |
| Frontend dependencies | `frontend/bun.lock` | Renovate lockfile maintenance weekly; majors separate |
| Dagger engine | `engineVersion` in `dagger.json` and every workflow's `version:` input, kept equal | Renovate, its own PR |
| Universal Viewer fork | `UV4_REF` commit in `.docker/htrflow-web.dockerfile` | Renovate, its own PR — `.docker/uv4-uv-html.patch` may need re-deriving |
| htrflow source for the source-built base | the commit env var in `ci.yml` and `publish.yml`, the same in both | Renovate, its own PR — the base and the wrapper on it must be re-verified |
| Upstream htrflow base image | the `base-…` stage `FROM` in the wrapper dockerfile | by hand for tag bumps (a deliberate, tested pin); Renovate refreshes the digest only |
| torch / torchvision | explicit versions per base stage in the wrapper dockerfile | by hand, following the CUDA wheel index named there |
| Kueue, Kyverno | `KUEUE_VERSION`, `KYVERNO_CHART_VERSION` in the `Makefile` | by hand |

Inside the builds, dagger containers sync with `uv sync --frozen
--all-packages`, and the wrapper image installs its dependencies from
`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build instead of resolving freshly. Renovate raises security updates at any
time, outside the weekly schedule.
