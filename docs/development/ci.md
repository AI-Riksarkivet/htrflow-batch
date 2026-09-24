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
| `check-chart` | `helm lint` and renders of both charts (defaults, `ci/full-values.yaml`, and the production profile from `ci/prod-values.yaml`), then `kubeconform -strict` on every render and on the converter's skeletons, Kueue and Kyverno kinds against pinned CRD schemas (`scripts/crd-schemas.sh`). What the renders contain is `test_chart_render.py`'s |
| `test` | the workspace pytest suite in a uv container (`uv run --no-sync pytest`, no GPU) — wrapper, converter, web |
| `test-driver` | the level-0 pin test ([Testing](testing.md)) against a wrapper image it builds itself; `make test-driver-real` runs it on an image already in the local docker daemon |
| `build-wrapper` | the wrapper image from `.docker/htrflow-batch.dockerfile`, for the engine's own platform ([Releasing](releasing.md#one-dockerfile-every-architecture)). `--transformers-version` picks the other transformers line ([Model handling](../how-it-works/wrapper.md#model-handling)) |
| `build-web` | the web image from `.docker/htrflow-web.dockerfile` (CPU-only, no torch): the campaign browser SPA, the Universal Viewer fork at the pinned `UV4_REF` with `.docker/uv4-uv-html.patch` applied, and the read API that serves both. A CA bundle goes in as the optional `ca` build secret |
| `build-campaigns` | the converter image from `.docker/htrflow-campaigns.dockerfile` (distroless, CPU-only, no git binary or shell): the `htrflow-campaigns` CLI and dulwich, what the Argo CD hook in a campaigns repository runs ([Campaign YAML](../reference/campaign-yaml.md)) |
| `scan` | Trivy over the built wrapper image; fails on findings (default `CRITICAL,HIGH`, unfixed ignored). Every Trivy run reads the VEX statements in `.docker/distroless.openvex.json` ([Releasing](releasing.md)) |
| `scan-web` | the same over the built web image — a distroless Debian runtime with no shell or package manager, so a clean gate is realistic; `make scan-web` is the local twin |
| `scan-campaigns` | the same over the built converter image, which `ci.yml` gates on pull requests too |
| `scan-json` | `scan` with JSON output that never fails the call; what `make scan` runs |
| `scan-sarif` | Trivy over one built image (`--image wrapper\|web\|campaigns`) as a SARIF report: `CRITICAL,HIGH`, unfixed findings included, never fails on findings; what `security.yml` uploads to the Security tab, while `scan`, `scan-web` and `scan-campaigns` stay the gates |
| `scan-published` | Trivy over a published image by the digest this commit pins (`--image wrapper\|web\|campaigns`, `--arch`), pulled, not rebuilt: what clusters run now, where the scans above cover the next release |
| `verify-published` | the chart's verify-images `ClusterPolicy`, rendered with `values-prod.yaml`, run by the Kyverno CLI against those three pinned digests: their Sigstore signatures and transparency-log entries, checked the way the admission webhook checks them |
| `publish-docker` | refuses a tag already on the registry, then tests, builds, runs the driver test (wrapper) and the Trivy CRITICAL gate on the image it will push, pushes it (`--component wrapper\|web\|campaigns`) and returns its reference with the digest; passes `--base-revision` and `--transformers-version` on to the wrapper build ([Releasing](releasing.md#publishing)) |
| `check-tag-free` | `publish-docker`'s "never overwrite a tag" check on its own: fails when the tag (or, with `--tag-suffix`, the suffixed or the bare tag) is on the registry or the registry gives no answer |
| `compose-up` | starts the `web` service of the `.docker/docker-compose.yml` project as a dagger Service |
| `compose-test` | brings up the compose stack and fetches `/uv.html` from the web image the compose file pins; `make compose-smoke` runs the stack on images built from the checkout |

The converter is a pure Python package as well as an image. A campaigns
repository's own CI and a workstation install it with `uvx --from
"git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter"
htrflow-campaigns`; the image (`build-campaigns`) is only for the Argo CD
hook, which runs in the cluster where nothing can be installed.

## What the containers see: exclude, not include

`lint`, `typecheck` and `test` build their container with `buildWithUv` in
`.dagger/main.go`, which mounts the **whole repository minus `repoExclude`**:
VCS and worktree plumbing, `.gitignore`'s entries, and dependency and build
caches. `check-frontend` does the same for the `frontend/` subtree with its
own short exclude list.

A new path is therefore visible by default, and a missing exclude is a
harmless over-inclusion.
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

The same file reaches `make build-web` (the `ca` build secret),
`make test-driver-real` and the frontend targets. Without it, behind such a
proxy, the viewer's `npm install` fails with an npm-internal crash rather
than a certificate error, since node does not read the system store.

## Makefile targets

- **Workspace:** `install`, `format`, `lint`, `check` (format + lint),
  `test`, `typecheck`, `test-driver-real`, `ci` (typecheck, then dagger
  `checks` and `test`), `clean`.
- **Images:** `build` (`dagger call build-wrapper`), `build-wrapper` and
  `build-web` and `build-campaigns` (plain `docker build`, tagged `$(HTR_REGISTRY)/…:$(IMAGE_TAG)`),
  `poc-push` (the wrapper and web builds, pushed to the dev registry, digests printed),
  the htrflow base built from a checkout and the refresh of its committed
  lock (`lock-htrflow-base`, [Dev cluster](dev-cluster.md#the-gpu-wrapper-image)), `scan` (dagger
  `scan-json`), `scan-web` (Trivy, HIGH/CRITICAL with a fix fails),
  `scan-image` (Trivy over an image in the local docker daemon, CRITICAL
  with a fix fails; how CI gates the wrapper it builds with plain `docker build`),
  `publish` (manual, needs `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`; refuses
  an existing tag).
- **Compose:** `compose-up`, `compose-test`, `compose-smoke` (both images
  built from the checkout, then the smoke — see [Testing](testing.md)),
  `compose-smoke-run` (the smoke on any `WRAPPER_IMAGE` / `WEB_IMAGE`),
  `compose-down`.
- **Charts:** `helm-lint`, `helm-template` (lint, render both charts on
  defaults and `ci/full-values.yaml`, kubeconform).
- **Cluster:** `install-kueue` (Kueue's Helm chart at `KUEUE_VERSION`),
  `install-kyverno` (the admission
  controller `security.policies.enabled` needs, at
  `KYVERNO_CHART_VERSION`), `install-devstack`, `psa-labels`,
  `campaigns-apply` (render a campaigns repo, server-side apply its
  pipelines then its campaigns, sync each campaign's pause; `PRUNE=1` adds
  `--prune`), `e2e` (validate, apply, then block until every campaign Job
  reaches a terminal condition; a Failed Job fails the target).
- **Documentation:** `docs-serve`, `docs-build` (through
  `scripts/docs-site.sh`), `config-reference` (regenerates
  `docs/reference/configuration.md`; a test asserts the committed page
  equals it). Slides: `scripts/slides.sh` builds the Marp decks at the top of
  `docs/slides` into `site/slides` after the site build (HTML and PDF, linked
  from Presentations). CI renders no diagrams, so render and commit a changed
  diagram locally; superseded decks go in `docs/slides/archive`.
- **Contracts:** `api-contract` (regenerates
  `frontend/src/lib/fixtures/api-contract.json` from real read-API output; a
  test asserts the committed fixture equals it — see
  [Testing](testing.md#the-generated-files-ci-checks-are-current)).
- **Frontend:** `frontend-install`, `frontend-test`, `frontend-check`,
  `frontend-build`, `frontend-dev`.

The cluster constants these targets use come from `.env`
([Dev cluster](dev-cluster.md#env)).

## Workflows

- **`ci.yml`** ("Tests") — on push to `main`, on pull requests and by hand.
  The `ci` job runs `dagger call checks`, `dagger call test` and the
  `scripts/loc-budget.sh` line budgets. A `scan-campaigns` job runs the
  converter image's CRITICAL gate on every trigger, pull requests included:
  it is a small distroless build with nothing to clone. Two scan jobs, `scan-web` and
  `scan-wrapper`, run `scan-web` and `scan` with `--severity CRITICAL
  --ignore-unfixed` on pushes to `main` and manual runs only (`scan-wrapper`
  then runs `test-driver` on the same build): each has to
  build its image first, and both builds are expensive (the wrapper's CUDA
  base; the web image's viewer clone and npm and bun builds). A pull request
  that changes a dockerfile gets its scan when it lands on `main`, before
  any image is published from it. A further job, on every trigger, builds the
  htrflow base and the wrapper on a native runner of the second architecture,
  runs the level-0 pin test on it and (on `main`) `make scan-image`, and
  pushes nothing.
- **`publish.yml`** — manual, one explicit tag per run; tests, builds,
  pushes, signs and attests all three images for both of the CPU architectures
  they ship for — each on a runner of its own architecture, joined into one
  manifest list per image ([Releasing](releasing.md#the-publish-workflow)).
- **`ci.yml`** also runs, on every trigger: a `docs` job, the lint (the
  site's pages, the root and package READMEs and `SECURITY.md`: no project
  ids, dates, versions, hardware or one site's hosts) and the strict site
  build below without the deploy, so a pull request that breaks
  the site fails before it lands; `verify-published`; and a `dagger-go` job,
  `go vet` and `go test` over `.dagger/publishcheck` ([Testing](testing.md)).
- **`docs.yml`** ("Documentation") — on push to `main` and by hand:
  `uv sync --locked --only-group docs` (zensical pinned and hash-checked in
  `uv.lock`), `scripts/docs-site.sh build --clean --strict` with that
  zensical, then deploy to GitHub Pages.
- **`security.yml`** ("Security") — weekly, by hand, and on pushes to `main`
  that change an image's inputs. One job per image (wrapper, web and
  campaigns): `scan-sarif` uploads the
  Trivy report to the Security tab, then the same CRITICAL gate as `ci.yml`
  runs, so an advisory published between changes fails a scheduled run. A
  further job does the same for the wrapper on the second architecture, on a
  native runner of it, through `make scan-image`. On a
  push the gate is skipped, since `ci.yml` has just run it.
- **`published.yml`** ("Published images") — weekly, by hand, and on pushes
  to `main` that move a pin (the chart's web image, the demo pipeline's
  wrapper, the Argo CD hook's converter image). It builds nothing:
  `scan-published` gates each pinned digest on both architectures, and
  `verify-published` checks their signatures, so a signature that stops
  verifying fails a scheduled run too.
- **`codeql.yml`** ("CodeQL") — on push and pull request to `main` and weekly:
  static analysis of the Python, frontend, dagger and workflow code.
- **`trufflehog.yml`** ("Secret Leaks") — on every push and pull request:
  TruffleHog over the git history; `.github/trufflehog-exclude-paths.txt`
  names the paths whose history holds dummy test credentials.
- **`scorecard.yml`** — OpenSSF Scorecard weekly, on push to `main` and on
  branch-protection changes, publishing the score the README badge shows.

No workflow uses the `dagger-for-github` action: it installs the CLI with a
piped install script. `.github/actions/setup-dagger` downloads the CLI's
GitHub release asset, checks it against the SHA-256 committed there, and
starts the engine from its image digest; steps then run `dagger call`
directly, with their arguments passed through `env:`. The action's version
must equal `engineVersion` in `dagger.json` (a test asserts it).

## Dependency pins

Every input is pinned. **Dependabot** (`.github/dependabot.yml`) proposes
version updates weekly — one grouped pull request per ecosystem, a new major
on its own, and nothing younger than a week — for the pins a registry can
answer for: the actions, `uv.lock`, `frontend/bun.lock`, the dockerfiles'
`FROM` lines and the dagger module's `go.mod`. Its security updates, enabled
in the repository's settings, come as soon as an advisory is published.
Every such pull request goes through the full CI. Patch and minor updates of the
actions, `uv.lock` and `frontend/bun.lock` merge themselves once every required
check passes (`.github/workflows/dependabot-automerge.yml`); a major version, a
base image and the dagger module are merged by hand, because a new major is a
migration and pull-request CI does not build the amd64 wrapper or the web image.
Everything else moves by hand, in a pull request of its own.

| Pin | Lives in | Updated by |
|---|---|---|
| GitHub Actions | `uses:` lines in the workflows and the composite actions, by commit SHA with the version as a comment | Dependabot: the SHA and the comment together |
| Base images | `FROM` lines in `.docker/*.dockerfile`, as tag plus digest | Dependabot, within the major (and, for the CUDA base, within the CUDA release line); a new major by hand |
| Tool and chart images | image values in both charts' `values.yaml`, `.docker/docker-compose.yml`, the image constants in `.dagger/main.go`, digest-pinned `run:` images in workflows and actions, `TRIVY_IMAGE` in the `Makefile` — each as tag plus digest | by hand: `docker buildx imagetools inspect <ref>` gives the digest of the new tag |
| Python dependencies | `uv.lock` (workspace) | Dependabot; by hand, `uv lock --upgrade` (or `--upgrade-package <name>`) |
| Frontend dependencies | `frontend/bun.lock` | Dependabot; by hand, `bun update` in `frontend/` |
| Dagger engine | `engineVersion` in `dagger.json`; the CLI version, its checksums and the engine digest in `.github/actions/setup-dagger`; the SDK modules in `.dagger/go.mod` | by hand, all in one PR: a test fails until the action's version equals `engineVersion`. Dependabot moves the module's other Go dependencies |
| Universal Viewer fork | `UV4_REF` commit in `.docker/htrflow-web.dockerfile` | by hand, its own PR — `.docker/uv4-uv-html.patch` may need re-deriving |
| htrflow source for the wrapper's base | `ARG HTRFLOW_REF` in the wrapper dockerfile | by hand, its own PR, with `make lock-htrflow-base` — the lock, the base and the wrapper on it must be re-verified |
| Dependencies of the wrapper's base, torch included | `.docker/htrflow-base/`: htrflow's `pyproject.toml` plus `overlay.toml`, and `uv.lock`, installed with `uv sync --locked` | by hand with `make lock-htrflow-base`, when the htrflow commit or the overlay moves |
| transformers line | `.docker/transformers/<major>.in`, compiled with hashes into `<major>.txt` | by hand, `make transformers-requirements`; the dockerfile's `TRANSFORMERS_VERSION` default with it, and the build fails while they disagree |
| torch / torchvision | per architecture in `.docker/htrflow-base/overlay.toml` (`constraint-dependencies`, and the CUDA wheel index as a source) | by hand, then `make lock-htrflow-base` |
| Kueue, Kyverno | `KUEUE_VERSION`, `KYVERNO_CHART_VERSION` in the `Makefile`, and the CRD schemas `check-chart` validates against (`scripts/crd-schemas.sh`) | by hand, together |
| Released images | the three manifest-list digests a release commit pins ([Releasing](releasing.md#the-publish-workflow)) | by the release commit |

Inside the builds, dagger containers sync with `uv sync --frozen
--all-packages`, and the wrapper image installs its dependencies from
`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build instead of resolving freshly.
