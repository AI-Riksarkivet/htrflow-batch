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
| `test-driver` | `packages/wrapper/tests/test_driver_real.py` against the real htrflow inside a wrapper image it builds itself — the level-0 pin test ([Testing](testing.md)); `ci.yml` runs it in the wrapper scan job. `make test-driver-real` runs the same test against an image that already exists in the local docker daemon, which is how the second architecture's CI job runs it on the image it just built |
| `build-wrapper` | the wrapper image from `.docker/htrflow-batch.dockerfile`, for the engine's own platform. The optional `--platform` exists for a caller with an engine per platform; nothing here passes it ([Releasing](releasing.md#one-dockerfile-every-architecture)). `--transformers-version` builds the image on the other transformers line; empty keeps the dockerfile's default ([Two transformers lines](../how-it-works/wrapper.md#model-handling)) |
| `build-web` | the web image from `.docker/htrflow-web.dockerfile` (CPU-only, no torch): the campaign browser SPA, the Universal Viewer fork at the pinned `UV4_REF` with `.docker/uv4-uv-html.patch` applied, and the read API that serves both. A CA bundle goes in as the optional `ca` build secret |
| `build-campaigns` | the converter image from `.docker/htrflow-campaigns.dockerfile` (distroless, CPU-only, no git binary or shell): the `htrflow-campaigns` CLI and dulwich, what the Argo CD hook in a campaigns repository runs ([Campaign YAML](../reference/campaign-yaml.md)) |
| `scan` | Trivy over the built wrapper image; table output, fails on findings (default `CRITICAL,HIGH`, unfixed findings ignored). Every Trivy run here, and `make scan-web` and `make scan-image`, reads the VEX statements in `.docker/distroless.openvex.json`: findings in the distroless runtime that Debian has no fix for, each statement pinned to one exact package version, so it stops applying by itself when that package changes |
| `scan-web` | the same over the built web image — a distroless Debian runtime with no shell or package manager, so a clean gate is realistic; `make scan-web` is the local twin |
| `scan-campaigns` | the same over the built converter image, which `ci.yml` gates on pull requests too |
| `scan-json` | `scan` with JSON output that never fails the call; what `make scan` runs |
| `scan-sarif` | Trivy over one built image (`--image wrapper\|web\|campaigns`) as a SARIF report: `CRITICAL,HIGH`, unfixed findings included, never fails on findings; what `security.yml` uploads to the Security tab, while `scan`, `scan-web` and `scan-campaigns` stay the gates |
| `scan-published` | Trivy over a published image by reference (`--image wrapper\|web\|campaigns`, `--platform linux/amd64\|linux/arm64`): the digest this commit pins — the chart's `web.image`, the demo pipeline's wrapper, the Argo CD hook's converter image — pulled from the registry, not rebuilt. The scans above say what the next release will carry; this one says what clusters run now |
| `verify-published` | the chart's verify-images `ClusterPolicy`, rendered with `values-prod.yaml`, run by the Kyverno CLI against those three pinned digests: their Sigstore signatures and transparency-log entries, checked the way the admission webhook checks them |
| `publish-docker` | refuses a tag already on the registry, then tests, builds, runs the driver test (wrapper) and the Trivy CRITICAL gate on the image it will push, pushes it (`--component wrapper\|web\|campaigns`) and returns its reference with the digest; passes `--base-revision` and `--transformers-version` on to the wrapper build ([Releasing](releasing.md#publishing)) |
| `check-tag-free` | `publish-docker`'s "never overwrite a tag" check on its own: fails when the tag (or, with `--tag-suffix`, the suffixed or the bare tag) is on the registry or the registry gives no answer |
| `compose-up` | starts the `web` service of the `.docker/docker-compose.yml` project as a dagger Service |
| `compose-test` | brings up the compose stack and fetches the web service's `/uv.html`. The module mounts only `.docker/` as the compose project, so the `web` service is image-only: this checks the release the compose file pins; `make compose-smoke` runs the stack on images built from the checkout |

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
  equals it). Slides live in `docs/slides` and build with
  `scripts/slides.sh`: they are Marp sources rather than site pages, so
  `scripts/docs-site.sh` stages them out the way it stages out the stories,
  and `slides.sh` writes into `site/slides` after the site build, which
  clears `site/` first. The documentation workflow runs both, so every deck
  at the top of `docs/slides` is published as HTML and PDF and linked from
  the Presentations page; `slides.sh` lints the decks with the site's own
  content rules first, and in CI renders no diagrams (`MERMAID=skip`), so a
  changed diagram must be rendered and committed locally. Superseded decks
  go in `docs/slides/archive`, which is neither linted nor published.
- **Contracts:** `api-contract` (regenerates
  `frontend/src/lib/fixtures/api-contract.json` from real read-API output; a
  test asserts the committed fixture equals it — see
  [Testing](testing.md#the-two-generated-files-ci-checks-are-current)).
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
  any image is published from it; one job per image so a failure in one
  still builds the other. Another job runs on every trigger, pull requests
  included, on a native runner of the second architecture the wrapper ships
  for: it builds the htrflow base from source at the pinned htrflow commit
  and the wrapper on top of it, runs the level-0 library-API pin test against
  the image it just built, on pushes to `main` and manual runs the same
  Trivy CRITICAL gate (`make scan-image`), pushes nothing, and prints the
  image's base labels — so both architectures of a dockerfile change are proven before
  it lands, and the canary for an htrflow release that moves the library API
  finally runs on every push instead of waiting to be remembered.
- **`publish.yml`** — manual, one explicit tag per run; tests, builds,
  pushes, signs and attests all three images for both of the CPU architectures
  they ship for — each on a runner of its own architecture, joined into one
  manifest list per image ([Releasing](releasing.md#the-publish-workflow)).
- **`ci.yml`** also runs, on every trigger: a `docs` job, the lint and the
  strict site build below without the deploy, so a pull request that breaks
  the site fails before it lands; and `verify-published`.
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
  push the gate is skipped, since `ci.yml` has just run it. The published
  digests get jobs of their own: `scan-published` for each image on both
  architectures, a gate on every trigger (a push that changes the pins is
  the release commit), and `verify-published`, so a signature that stops
  verifying fails a scheduled run too.
- **`codeql.yml`** ("CodeQL") — on push and pull request to `main` and weekly:
  static analysis of the Python packages, the campaign browser, the dagger
  module and the workflows themselves, with findings in the Security tab.
- **`trufflehog.yml`** ("Secret Leaks") — on every push and pull request:
  TruffleHog over the git history, reporting verified and unverifiable
  credentials. A newly pushed branch is scanned from its first commit.
  `.github/trufflehog-exclude-paths.txt` names the few exact paths whose
  history holds dummy credentials from tests.
- **`scorecard.yml`** — OpenSSF Scorecard weekly, on push to `main` and on
  branch-protection changes, publishing the score the README badge shows.

No workflow uses the `dagger-for-github` action: it installs the CLI with a
piped install script. `.github/actions/setup-dagger` downloads the CLI's
GitHub release asset, checks it against the SHA-256 committed there, and
starts the engine from its image digest; steps then run `dagger call`
directly, with their arguments passed through `env:`. The action's version
must equal `engineVersion` in `dagger.json` (a test asserts it).

## Dependency pins

Every input is pinned. `renovate.json` holds the policy for moving the
pins, which [Renovate](https://docs.renovatebot.com/) applies once its app
is installed on the repository; until it is, nothing reads that file, and
every pin in the table below moves by hand. GitHub's Dependabot security
updates are on, and raise pull requests for vulnerable Python and frontend
dependencies in the meantime. This is where each kind of pin lives and how
it moves.

| Pin | Lives in | Updated by |
|---|---|---|
| GitHub Actions | `uses:` lines, by commit SHA with the version as a comment | Renovate, one grouped weekly PR |
| Base and tool images | `FROM` lines in `.docker/*.dockerfile`, image values in both charts' `values.yaml`, `.docker/docker-compose.yml`, the image constants in `.dagger/main.go`, digest-pinned `run:` images in workflows, `TRIVY_IMAGE` in the `Makefile` — each as tag plus digest | Renovate, one grouped weekly PR (regex managers cover the dagger module, workflows and Makefile) |
| Python dependencies | `uv.lock` (workspace) | Renovate lockfile maintenance weekly; runtime minor and patch grouped, majors and dev tools in their own PRs |
| Frontend dependencies | `frontend/bun.lock` | Renovate lockfile maintenance weekly; majors separate |
| Dagger engine | `engineVersion` in `dagger.json`; the CLI version, its checksums and the engine digest in `.github/actions/setup-dagger` | Renovate bumps `dagger.json` in its own PR; the action's three pins follow by hand in that PR, and a test fails until they do |
| Universal Viewer fork | `UV4_REF` commit in `.docker/htrflow-web.dockerfile` | Renovate, its own PR — `.docker/uv4-uv-html.patch` may need re-deriving |
| htrflow source for the wrapper's base | `ARG HTRFLOW_REF` in the wrapper dockerfile | Renovate, its own PR — the lock, the base and the wrapper on it must be re-verified |
| Dependencies of the wrapper's base, torch included | `.docker/htrflow-base/`: htrflow's `pyproject.toml` plus `overlay.toml`, and `uv.lock`, installed with `uv sync --locked` | by hand with `make lock-htrflow-base`, when the htrflow commit or the overlay moves |
| transformers line | `.docker/transformers/<major>.in`, compiled with hashes into `<major>.txt` | by hand, `make transformers-requirements`; the dockerfile's `TRANSFORMERS_VERSION` default with it, and the build fails while they disagree |
| torch / torchvision | per architecture in `.docker/htrflow-base/overlay.toml` (`constraint-dependencies`, and the CUDA wheel index as a source) | by hand, then `make lock-htrflow-base` |
| Kueue, Kyverno | `KUEUE_VERSION`, `KYVERNO_CHART_VERSION` in the `Makefile` | by hand |

Inside the builds, dagger containers sync with `uv sync --frozen
--all-packages`, and the wrapper image installs its dependencies from
`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build instead of resolving freshly. With the app installed, Renovate raises
security updates at any time, outside the weekly schedule.
