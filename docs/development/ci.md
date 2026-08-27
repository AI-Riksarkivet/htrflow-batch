# CI

CI logic lives in a dagger module (`.dagger/`, Go), not inline in YAML — the
GitHub Actions workflows are thin wrappers that call `dagger call <function>`.
That means the exact same commands run locally and in CI. `dagger functions`
lists what the module exposes on your checkout; the table below is what it
lists at the tip of the audit-remediation branch, with the CI work package
(B1) items marked **planned** where they have not landed on the branch you
are reading.

## Dagger functions

| Function | Does |
|---|---|
| `checks` | `ruff format --check` + `ruff check` on the workspace, plus `helm lint` on the chart. **Planned (B1):** ruff through `uv run --no-sync` so the locked version runs (today `uvx` pulls the latest); a `typecheck` step; a chart render on defaults and `ci/full-values.yaml` + `kubeconform -strict` (the `make helm-template` equivalent); and `check-frontend` |
| `check-frontend` | **Planned (B1):** `bun install --frozen-lockfile && bun run lint && bun run check && bun run test && bun run build` in an `oven/bun` container (CA bundle wired as for uv) |
| `typecheck` | **Planned (B1):** `ty check` on both packages, what `make typecheck` runs locally |
| `test` | workspace pytest suite in a uv container (`uv run --no-sync pytest`, no GPU required) |
| `test-driver` | **Planned (B1, opt-in):** `test_driver.py` against the real htrflow inside the built wrapper image — the level-0 pin test ([Testing](testing.md)) |
| `build` | production wrapper image from `.docker/htrflow-batch.dockerfile` |
| `build-reconciler` | reconciler image from `.docker/htrflow-reconciler.dockerfile` (CPU-only, no torch) |
| `build-viewer` | reproducible UV4 viewer image: clone the Riksarkivet `universalviewer4` fork at a pinned ref, apply `.docker/uv4-uv-html.patch`, build it with npm (UV's own toolchain), layer onto `nginxinc/nginx-unprivileged:1.27-alpine` — and bun-build the campaign browser SPA from `frontend/` on top, so `/` is the SPA and `/uv.html` is UV |
| `scan` | Trivy scan of the built wrapper image (table output, exits non-zero on findings — not wired into `ci.yml`, since the CUDA/ubuntu base will never be alpine-clean). The reconciler image is scanned by `make scan-reconciler` (Trivy 0.65.0, HIGH/CRITICAL, `--ignore-unfixed`) |
| `scan-json` | same scan, JSON output, never fails the call |
| `publish-docker` | tests, builds, and pushes an image (`--component wrapper\|viewer`) to a registry; validates the tag against `packages/wrapper/pyproject.toml`'s version unless `--skip-validation` |
| `compose-up` | starts the `.docker/docker-compose.yml` stack as a dagger Service |
| `compose-test` | brings up the compose stack and curls the viewer's `uv.html` — needs registry-pullable images |

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
`build-wrapper`, `build-reconciler`, `scan`, `scan-reconciler`, `publish`
(manual, `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`), `compose-up`,
`compose-test`, `compose-smoke` (the verified local path — see
[Testing](testing.md)), `compose-down`, `helm-lint`, `helm-template`
(lint + render on defaults and `ci/full-values.yaml` + kubeconform),
`docs-serve`, `docs-build` (`uvx zensical`), `poc-push` / `poc-push-arm64`
(build + push the wrapper and reconciler images into the in-cluster k3s
registry, printing the digests to pin), `warmup`, `psa-labels`,
`frontend-install/test/check/build/dev`, `viewer-image`, `clean`. The
cluster-local constants they use come from `.env`
([Local k3s development](local-k3s.md)).

## Workflows

- **`ci.yml`** ("Tests") — on push to `main` and on pull requests: `dagger
  call checks` then `dagger call test`. The dagger action is SHA-pinned; its
  engine `version` is `latest` (**planned (B1):** pin it to the module's
  engine version).
- **`publish.yml`** — manual (`workflow_dispatch`) only: `dagger call
  publish-docker --component wrapper ...` using `DOCKERHUB_USERNAME`/
  `DOCKERHUB_TOKEN` secrets. Image signing/attestation (cosign + SLSA) was
  removed when the build moved to dagger publish. **Planned (B1):** a
  component × arch matrix (wrapper, reconciler, viewer), an explicit tag
  input instead of re-pushing an immutable version, and cosign keyless
  signing of the pushed digest — which is what `security.verifyImages` in
  the chart verifies ([Chart Values](../reference/chart.md#trust-boundary-security)).
- **`docs.yml`** ("Documentation") — manual only while the repo is private
  (GitHub Pages isn't available for private repos on the free plan, so a
  push trigger would fail every run): `pip install zensical` and `zensical
  build --clean`, then deploy to GitHub Pages. Restore the push trigger once
  the repo goes public.

## Dependency pins

Actions are SHA-pinned, `uv sync --locked` builds the images, the
dockerfiles pin base images and the uv binary by digest and torch by
version, and the chart pins every devStack image by digest. **Planned
(B1):** a `renovate.json` so those digests and the GitHub `git` CIDRs get
update PRs instead of rotting.
