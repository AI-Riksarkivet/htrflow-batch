# Deployment

## Building images

Two images: the GPU **wrapper** (`.docker/htrflow-batch.dockerfile`) and
the CPU-only **web front** (`.docker/htrflow-web.dockerfile`) — the read
API, the campaign browser and the Universal Viewer in one. Reproducibly,
through the dagger module:

```bash
dagger call build-wrapper          # wrapper image, from .docker/htrflow-batch.dockerfile
dagger call build-web              # web image: bun SPA + patched UV4 + the read API
```

**One wrapper dockerfile, two architectures.** It declares two base stages
and the runtime stage picks one: `base-amd64` is the digest-pinned upstream
`airiksarkivet/htrflow` plus a cu128 torch swap (Blackwell sm_120 kernels);
`base-arm64` is `${HTRFLOW_ARM64_BASE}`, an htrflow base built from source
because upstream publishes no arm64 image, plus the three extras that arch
needs (gcc + Python headers for triton's runtime JIT, `sentencepiece`, and
a `transformers` 4.x pin). Everything arch-specific sits behind
`if [ "$TARGETARCH" = "arm64" ]`, and BuildKit resolves only the base stage
the target needs — the amd64-only image is never even looked up on an arm64
host.

Nothing passes `--platform`, ever: `uv` segfaults under `qemu-x86_64` and an
emulated GPU image cannot be smoke-tested. Each image is built on a machine
of its own architecture — `docker build` reads `TARGETARCH` from the host,
CI puts the arm64 build on an `ubuntu-24.04-arm` runner.

The **converter is not an image** — it is a plain Python package that runs
in the campaigns repo's own CI or on a laptop, installed with `uvx`:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter" \
  htrflow-campaigns --help
```

`dagger call build-wrapper` is heavy the first time — the base
(`airiksarkivet/htrflow` + cu128 torch) is ~10 GB — but the dagger engine
cache makes subsequent builds fast. `build-web` calls the dockerfile, whose
second stage clones the Riksarkivet `universalviewer4` fork at a pinned
commit (`UV4_REF`), applies `.docker/uv4-uv-html.patch`, builds UV with npm
(its own toolchain), and layers the result plus the bun-built campaign
browser onto the Python runtime as `/app/static`. See [CI](ci.md) for the
full function table.

Every input is pinned (audit W8/S7) — with one honest limit: the arm64 base's `HTRFLOW_ARM64_BASE_REF` pins htrflow's *source*, not its dependency resolution (its lockfile is gitignored), so the wrapper's own torch/torchvision pins are what catch drift there. Base images and the uv binary by
digest, torch/torchvision by version per arch, the wrapper's dependencies
from the workspace lock with hashes (`uv export --locked …
--require-hashes`, so a stale `uv.lock` fails the build). The arm64 base is
a local tag with no registry digest, so `--build-arg
HTRFLOW_BASE_REVISION=…` is how it is recorded: stamped as the OCI label
`se.riksarkivet.htrflow.base.revision` — `manifest.json` only knows the
package version (`0.2.6`), the label says which htrflow commit the base
really carries. Each base stage carries its own default (the upstream tag
on amd64), and the runtime stage inherits the labels of whichever base it
was built from, so an un-passed build arg still tells the truth.

For fast local-only iteration against a bare-k3s PoC's in-cluster registry
(no dagger, no push credentials):

```bash
make poc-push                  # build-wrapper + build-web, push both, print their digests
make build-htrflow-base-arm64  # the arm64 base the wrapper builds on (aarch64 hosts)
make build-web                 # just the web image
make scan-web                  # Trivy (0.65.0), HIGH/CRITICAL with a fix fails
```

`poc-push` builds whatever the host is. On an `aarch64` host the wrapper
picks the `base-arm64` stage and stamps `HTRFLOW_BASE_REVISION` from the
`HTRFLOW_DIR` checkout (`.env`, default `~/htrflow`, `git describe --tags
--always --dirty`); on amd64 it builds on the upstream base.
`make poc-push-arm64` still exists as a deprecated alias of `poc-push` and
says so. The registry, tag
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

`--component` is `wrapper` (default) or `web`. `publish-docker`
runs the test suite first and aborts on failure — it will not push an image
the tests don't pass. Tag resolution: an explicit `--tag` is validated
against `packages/wrapper/pyproject.toml`'s version unless
`--skip-validation` is set; an empty tag defaults to `"v" + <that version>`.

**Registry defaults:**

| Component | Default repository | Default registry |
|---|---|---|
| wrapper | `riksarkivet/htrflow-batch` | `docker.io` |
| web | `riksarkivet/htrflow-web` | `docker.io` |

Override with `--image-repository` / `--registry`. `--tag-suffix` appends
to the tag *after* it has been validated against the version — how one
publish run pushes `<tag>-amd64` and `<tag>-arm64`.

In CI, `publish.yml` is manual (`workflow_dispatch`) only and supplies
`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` from repository secrets. The
wrapper is published from two runners, each native to its architecture: the
amd64 image through dagger on `ubuntu-24.04`, the arm64 image with plain
`docker build` on `ubuntu-24.04-arm` (the dagger engine cannot see a base
image that exists only in that runner's docker daemon), and a third job
joins them into the manifest list `riksarkivet/htrflow-batch:<tag>` with
`docker buildx imagetools create`. Cosign signing and SLSA provenance run
for all three digests, an SBOM attestation for the two real images
([CI](ci.md#workflows)). The converter is never published as an image —
see [Building images](#building-images) above.

## Chart release notes

The chart (`charts/htrflow-batch`, version 0.4.0) is not yet published to a
chart repository — there is no packaging/release workflow for it. Install
directly from a checkout:

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set publicResultsBase=<browser-reachable results base URL> \
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
