# Deployment

## Building images

Three images: the GPU **wrapper** (`.docker/htrflow-batch.dockerfile`, amd64
on the upstream base; `.docker/htrflow-batch-gpu-arm64.dockerfile` on a
locally built arm64 base), the CPU-only **read API**
(`.docker/htrflow-web.dockerfile`) and the **viewer**
(`.docker/uv4-viewer.dockerfile`). Reproducibly, through the dagger module:

```bash
dagger call build                  # wrapper image (amd64), from .docker/htrflow-batch.dockerfile
dagger call build-web              # read API image
dagger call build-viewer           # UV4 viewer image, pinned upstream ref + patch + the SPA
```

The **converter is not an image** — it is a plain Python package that runs
in the campaigns repo's own CI or on a laptop, installed with `uvx`:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter" \
  htrflow-campaigns --help
```

`dagger call build` is heavy the first time — the base
(`airiksarkivet/htrflow` + cu128 torch) is ~10 GB — but the dagger engine
cache makes subsequent builds fast. `build-viewer` clones the Riksarkivet
`universalviewer4` fork at a pinned commit, applies
`.docker/uv4-uv-html.patch`, builds UV with npm (its own toolchain), and
layers the result plus the bun-built campaign browser onto
`nginxinc/nginx-unprivileged:1.27-alpine`. See [CI](ci.md) for the full
function table.

Every input is pinned (audit W8/S7): base images and the uv binary by
digest, torch/torchvision by version, the wrapper's dependencies from the
workspace lock with hashes (`uv export --locked … --require-hashes`, so a
stale `uv.lock` fails the build). Both wrapper dockerfiles take
`--build-arg HTRFLOW_BASE_REVISION=…`, stamped as the OCI label
`se.riksarkivet.htrflow.base.revision` — `manifest.json` only knows the
package version (`0.2.6`), the label says which htrflow commit the base
really carries.

For fast local-only iteration against a bare-k3s PoC's in-cluster registry
(no dagger, no push credentials):

```bash
make poc-push          # build-wrapper + build-web, push both, print their digests
make poc-push-arm64    # force the native arm64 GPU recipe regardless of host arch
make build-web         # just the read API image
make scan-web          # Trivy (0.65.0), HIGH/CRITICAL with a fix fails
make viewer-image      # bun build + docker build of the viewer (tag 127.0.0.1:30500/uv4:dev)
```

`poc-push` is architecture-aware: on an `aarch64` host it builds from the
arm64 GPU recipe and stamps `HTRFLOW_BASE_REVISION` from the `HTRFLOW_DIR`
checkout (`.env`, default `~/htrflow`, `git describe --tags --always
--dirty`); on amd64 it builds the upstream-based image. The registry, tag
(`IMAGE_TAG`, default `dev`) and the rest of the cluster constants come from
`.env` (copy `.env.example`). Each push prints the digest to pin in the
chart values, which refuse tags unless `security.allowTagImages=true`. The
arm64 base build itself is described in
[Local k3s development](local-k3s.md#the-arm64-gpu-wrapper-image).

## Publishing

```bash
dagger call publish-docker --component wrapper \
  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN
```

`--component` is `wrapper` (default), `viewer` or `api`. `publish-docker`
runs the test suite first and aborts on failure — it will not push an image
the tests don't pass. Tag resolution: an explicit `--tag` is validated
against `packages/wrapper/pyproject.toml`'s version unless
`--skip-validation` is set; an empty tag defaults to `"v" + <that version>`.

**Registry defaults:**

| Component | Default repository | Default registry |
|---|---|---|
| wrapper | `riksarkivet/htrflow-batch` | `docker.io` |
| viewer | `riksarkivet/htrflow-batch-viewer` | `docker.io` |
| api | `riksarkivet/htrflow-web` | `docker.io` |

Override with `--image-repository` / `--registry`. In CI, `publish.yml` is
manual (`workflow_dispatch`) only and supplies `DOCKERHUB_USERNAME` /
`DOCKERHUB_TOKEN` from repository secrets for a matrix over all three
components; cosign signing, SLSA provenance and an SBOM attestation run for
each ([CI](ci.md#workflows)). The converter is never published as an image —
see [Building images](#building-images) above.

## Chart release notes

The chart (`charts/htrflow-batch`, version 0.3.0) is not yet published to a
chart repository — there is no packaging/release workflow for it. Install
directly from a checkout:

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set viewer.image=<registry>/htrflow-batch-viewer@sha256:<digest> \
  --set web.image=<registry>/htrflow-web@sha256:<digest> \
  ...
make psa-labels        # once; reads security.psaEnforce from the installed release
```

`make helm-lint` (defaults and `ci/full-values.yaml`, for both
`charts/htrflow-batch` and `charts/htrflow-devstack`) runs as part of
`dagger call checks`, so lint failures block CI the same way ruff failures
do; `make helm-template` additionally renders both charts on both value
sets and runs kubeconform. Bump each chart's `Chart.yaml` `version` on every
template/values change and note it in that chart's README changelog — a
stable version hid 21 revisions of drift on the PoC, before the split.
`Chart.yaml`'s `icon` field is absent (flagged by `helm lint` as merely
"recommended") — worth filling in before a public release. See
[Deploy](../getting-started/deploy.md) for the full install flow, including
the production-shaped install and the PoC devStack replay.
