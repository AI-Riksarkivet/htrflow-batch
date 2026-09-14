# Releasing

## Building images

Two images: the GPU **wrapper** (`.docker/htrflow-batch.dockerfile`) and the
CPU-only **web front** (`.docker/htrflow-web.dockerfile`) — the read API, the
campaign browser and the Universal Viewer in one. Reproducibly, through the
dagger module:

```bash
dagger call build-wrapper          # the wrapper image
dagger call build-web              # the web image: bun-built SPA + patched viewer + the read API
```

`build-wrapper` is heavy the first time — the CUDA base is several gigabytes —
and the dagger engine cache makes later builds fast. The web dockerfile's
viewer stage clones the Universal Viewer fork at a pinned commit
(`UV4_REF`), applies `.docker/uv4-uv-html.patch` and builds it with npm, its
own toolchain; the final stage puts the viewer and the bun-built campaign
browser into the read API's `/app/static` — the viewer first, the SPA on
top, so `/` is the SPA and `/uv.html` is the viewer. [CI](ci.md) has the
full function table.

The **converter is not an image**. It is a plain Python package that runs in
the campaigns repo's own CI or on a workstation, installed with `uvx`:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter" \
  htrflow-campaigns --help
```

### One dockerfile, every architecture

The wrapper dockerfile declares one base stage per architecture, and the
runtime stage builds `FROM base-${TARGETARCH}`. BuildKit resolves only the
stage the target needs, so a base that does not exist for one architecture
is never looked up on another.

- **Where upstream htrflow publishes an image for the architecture**, the
  base is that image, pinned by tag and digest, with torch and torchvision
  swapped for pinned builds from the CUDA wheel index the dockerfile names.
- **Where it does not**, the base is an htrflow image built from an htrflow
  source checkout and passed in as a build argument, plus what that base
  lacks at runtime: a C compiler and Python headers (triton JIT-compiles a
  CPython extension on the GPU path) and `sentencepiece` (to convert
  slow-only tokenizers). torch and torchvision are pinned
  explicitly here too, so a base whose own resolution drifts fails the build
  instead of changing silently. Building that base is described in
  [Dev cluster](dev-cluster.md#the-gpu-wrapper-image).

Every architecture-specific step sits behind a `TARGETARCH` test. The
transformers line is not one of them: both architectures install it from the
`TRANSFORMERS_VERSION` build argument, whose default is the line upstream
htrflow is tested on ([Two transformers
lines](../how-it-works/wrapper.md#model-handling)).

**Each architecture is built natively.** Nothing passes `--platform`:
`uv` crashes in a cross-architecture build, and a GPU image built for a
foreign architecture cannot be smoke-tested on the machine that built it.
`docker build` reads `TARGETARCH` from the host, and CI puts each
architecture on a runner of its own.

### Provenance and reproducibility

Every input is pinned ([CI → Dependency pins](ci.md#dependency-pins)): base
images and the uv binary by digest, torch and torchvision by version per
base, and the wrapper's own dependencies from the workspace lock with hashes
(`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build). The pin on htrflow's source fixes its code, not its dependency
resolution — htrflow's lockfile is not committed — which is why the explicit
torch pins matter on the source-built base.

A source-built base is a local tag with no registry digest, so the build
argument `HTRFLOW_BASE_REVISION` (`git describe --tags --always --dirty` of
the htrflow checkout) records what it contains. It is stamped as the OCI
label `se.riksarkivet.htrflow.base.revision` and as an environment variable
the wrapper writes into every ALTO file. `manifest.json` carries only
htrflow's package version; the revision says which htrflow commit the base
really runs. Each base stage declares its own default, and the runtime stage
inherits the labels of the base it was built from, so a build that passes no
revision still reports the truth.

### Local builds

For fast iteration against a dev cluster's registry — no dagger, no push
credentials:

```bash
make poc-push                  # build-wrapper + build-web, push both to $(HTR_REGISTRY), print their digests
make build-wrapper             # just the wrapper image, for the host's architecture
make build-web                 # just the web image
make scan-web                  # Trivy over the web image; HIGH/CRITICAL with a fix fails
```

The registry, the tag (`IMAGE_TAG`, default `dev`) and the other cluster
constants come from `.env` ([Dev cluster](dev-cluster.md#env)). Each push
prints the digest to pin in the chart values, which refuse tags unless
`security.allowTagImages=true`.

## Publishing

```bash
dagger call publish-docker --component wrapper \
  --docker-username env:DOCKERHUB_USERNAME --docker-password env:DOCKERHUB_TOKEN
```

`make publish` runs exactly this for the wrapper. `--component` is `wrapper`
(default) or `web`. `publish-docker` runs the test suite first and aborts on
failure — it does not push an image the tests do not pass — then builds and
pushes, and returns the published reference with its digest.

**Tag resolution.** An explicit `--tag` must equal the version in
`packages/wrapper/pyproject.toml` (a leading `v` is ignored) unless
`--skip-validation` is set; an empty tag becomes `v<version>`. The images are
released as one set, so the web image takes the same tag. The resolved tag is
baked into both images as the `HTRFLOW_BATCH_VERSION` build argument — kept
as an environment variable and as the `org.opencontainers.image.version`
label — so the status page's header names what the operator deployed.
`make build-*` bakes `IMAGE_TAG`; an unstamped build says `dev`.
`--tag-suffix` is appended *after* validation, which is how one run pushes
per-architecture tags such as `<version>-<arch>`.

**Registry defaults:**

| Component | Default repository | Default registry |
|---|---|---|
| wrapper | `riksarkivet/htrflow-batch` | `docker.io` |
| web | `riksarkivet/htrflow-web` | `docker.io` |

Override with `--image-repository` and `--registry`. `--base-revision` sets
`HTRFLOW_BASE_REVISION` for the wrapper, and `--transformers-version` sets
`TRANSFORMERS_VERSION`: empty, the default, keeps the dockerfile's pin, so a
normal release publishes the same image it always did. Naming the other
transformers line publishes that tag on it instead — for models the default
line cannot read ([Two transformers
lines](../how-it-works/wrapper.md#model-handling)) — and, since one run
publishes one image, that is a tag of its own, not a second variant of an
existing one.

### The publish workflow

`.github/workflows/publish.yml` is manual (`workflow_dispatch`) only, with
one required input, the tag (`v<version>`, equal to the wrapper's
`pyproject.toml` version), and two optional ones: a base revision and a
transformers version, which reaches the dagger-built architecture as the
flag above and the other as a `docker build` argument. Registry
credentials come from the `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`
repository secrets.

1. **Tags are immutable.** Every job first checks that neither its own
   per-architecture tag nor the final tag exists on the registry, and
   refuses to run if one does. To fix a release, bump the version and
   publish a new tag.
2. **Wrapper and web, through dagger.** One job runs `publish-docker` for
   the web image and for the wrapper on the runner's native architecture,
   pushing the wrapper as `<version>-<arch>`.
3. **The wrapper for the other architecture, natively.** A second job on a
   runner of that architecture builds the htrflow base from source at the
   pinned htrflow commit, runs `dagger call test` (the same gate
   `publish-docker` applies), then builds the wrapper with plain
   `docker build` and pushes `<version>-<arch>`. It is not dagger because
   the dagger engine builds in its own cache and cannot see a base image
   that exists only in the runner's docker daemon.
4. **One multi-architecture tag.** A final job joins the per-architecture
   wrapper images into the manifest list `riksarkivet/htrflow-batch:<version>`
   with `docker buildx imagetools create`, so a pull by tag or by the list's
   digest resolves to the node's architecture. The chart and campaign
   pipelines reference this tag's digest.

### Signing, SBOM and provenance

Every pushed digest goes through the composite action
`.github/actions/sign-attest`, shared by all three jobs so they cannot
drift:

- a **cosign** signature over the digest, keyless through Sigstore OIDC —
  what the chart's `security.verifyImages` verifies
  ([Chart values](../reference/chart.md));
- a **SLSA build-provenance** attestation, pushed to the registry;
- an **SPDX SBOM** generated by Trivy and attested, for the per-architecture
  images and the web image. The manifest list gets no SBOM of its own; its
  member images carry the package lists.

Verify a published image against the workflow identity:

```bash
# signature (a current cosign; old releases report "no signatures found")
cosign verify docker.io/riksarkivet/htrflow-batch:<version> \
  --certificate-identity-regexp '^https://github\.com/AI-Riksarkivet/htrflow-batch/\.github/workflows/publish\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# build provenance
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<version> \
  -R AI-Riksarkivet/htrflow-batch

# SBOM, on a per-architecture image
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<version>-<arch> \
  -R AI-Riksarkivet/htrflow-batch --predicate-type https://spdx.dev/Document/<spdx-version>
```

## Chart releases

The charts (`charts/htrflow-batch`, `charts/htrflow-devstack`) are not
published to a chart repository; install them from a checkout
([Deploy](../getting-started/deploy.md)).

`dagger call checks` includes `check-chart` — lint and render of both charts
on their defaults and `ci/full-values.yaml`, then kubeconform — so a chart
that fails to lint or render blocks CI like a ruff failure;
`make helm-template` is the local twin. Bump a
chart's `Chart.yaml` `version` on every template or values change and add an
entry to that chart's changelog — the release history and upgrade notes live
in
[`charts/htrflow-batch/README.md`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-batch/README.md)
and
[`charts/htrflow-devstack/README.md`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/charts/htrflow-devstack/README.md).
A version that stays put while templates change hides drift between what is
installed and what is in git. `Chart.yaml` has no `icon` (`helm lint` calls
it recommended); fill it in before publishing a chart.
