# CI

CI logic lives in a dagger module (`.dagger/`, Go), not inline in YAML — the
GitHub Actions workflows are thin wrappers that call `dagger call <function>`.
That means the exact same commands run locally and in CI.

## Dagger functions

| Function | Does |
|---|---|
| `checks` | `ruff format --check` + `ruff check` on the wrapper, plus `helm lint` on the chart |
| `test` | wrapper pytest suite in a built container (no GPU required) |
| `build` | production wrapper image from `.docker/htrflow-batch.dockerfile` |
| `build-viewer` | reproducible UV4 viewer image: clone the Riksarkivet `universalviewer4` fork at a pinned ref, apply `.docker/uv4-uv-html.patch`, `npm build`, layer onto `nginxinc/nginx-unprivileged:1.27-alpine` — and bun-build the campaign browser SPA from `frontend/` on top, so `/` is the SPA and `/uv.html` is UV |
| `scan` | Trivy scan of the built wrapper image (table output, exits non-zero on findings — not wired into `ci.yml`, since the CUDA/ubuntu base will never be alpine-clean) |
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
TLS-intercepting proxies where it is required (see the viewer-build npm
`NODE_EXTRA_CA_CERTS` gotcha in the [test log](test-log.md)).

## Makefile targets

`install`, `format`, `lint`, `check` (format + lint), `test`, `ci` (dagger
`checks` + `test`), `build`, `build-viewer`, `scan`, `publish` (manual,
`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`), `compose-up`, `compose-test`,
`compose-smoke` (the verified local path — see [Testing](testing.md)),
`compose-down`, `helm-lint`, `docs-serve`, `docs-build`, `poc-push` (build +
push the wrapper image into the in-cluster k3s registry), `clean`.

## Workflows

- **`ci.yml`** ("Tests") — on push to `main` and on pull requests: `dagger
  call checks` then `dagger call test`.
- **`publish.yml`** — manual (`workflow_dispatch`) only: `dagger call
  publish-docker --component wrapper ...` using `DOCKERHUB_USERNAME`/
  `DOCKERHUB_TOKEN` secrets. Image signing/attestation (cosign + SLSA) was
  removed when the build moved to dagger publish; reintroduce by capturing
  the digest from the dagger call output if needed.
- **`docs.yml`** ("Documentation") — manual only while the repo is private
  (GitHub Pages isn't available for private repos on the free plan, so a
  push trigger would fail every run): `zensical build --clean` then deploy to
  GitHub Pages. Restore the push trigger once the repo goes public.
