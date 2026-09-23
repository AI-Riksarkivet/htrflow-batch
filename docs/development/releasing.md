# Releasing

## Building images

Three images: the GPU **wrapper** (`.docker/htrflow-batch.dockerfile`), the
CPU-only **web front** (`.docker/htrflow-web.dockerfile`) — the read API, the
campaign browser and the Universal Viewer in one — and the CPU-only
**converter** (`.docker/htrflow-campaigns.dockerfile`), distroless like the
web image, for the Argo CD hook Job that applies a campaigns repo
([campaign-yaml.md, "With Argo CD"](../reference/campaign-yaml.md#with-argo-cd)).
Reproducibly, through the dagger module:

```bash
dagger call build-wrapper          # the wrapper image
dagger call build-web              # the web image: bun-built SPA + patched viewer + the read API
dagger call build-campaigns        # the converter image
```

The web dockerfile clones the Universal Viewer fork at a pinned commit
(`UV4_REF`), applies `.docker/uv4-uv-html.patch` and builds it with npm; the
final stage puts the viewer and the bun-built SPA into the read API's
`/app/static`, so `/` is the SPA and `/uv.html` is the viewer. [CI](ci.md)
has the full function table.

The **converter package is also installable without an image** — a plain
Python package that runs in the campaigns repo's own CI or on a workstation,
installed with `uvx`:

```bash
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch#subdirectory=packages/converter" \
  htrflow-campaigns --help
```

### One dockerfile, every architecture

The wrapper dockerfile builds its own htrflow base, the same way on every
architecture, in three stages:

- **`htrflow-src`** — htrflow's source at the commit the dockerfile pins
  (`HTRFLOW_REF`), fetched by BuildKit. A local checkout can stand in for
  it ([Dev cluster](dev-cluster.md#the-gpu-wrapper-image)).
- **`htrflow-builder`** — htrflow's virtual environment, its dependencies
  installed with `uv sync --locked --no-build` against
  `.docker/htrflow-base/` and htrflow itself built into a wheel with a
  pinned build backend (see below): htrflow's own
  `pyproject.toml` with this repository's `overlay.toml` appended, and the
  lock both resolve to. The overlay pins torch and torchvision per
  architecture — builds from PyTorch's CUDA 12 wheel index where those
  carry the newest GPU kernels for CUDA 12 drivers, PyPI's elsewhere. The
  build refuses a checkout whose `pyproject.toml` is not the one the lock
  was made for.
- **`htrflow-base`** — the CUDA runtime image with that environment; the
  wrapper is installed on top of it.

There is no separate base image to build, pull or pass in, so every build
path runs this one recipe, and no step in it is architecture-specific. Both
architectures install the transformers line from the `TRANSFORMERS_VERSION`
build argument, whose default is the line upstream htrflow is tested on.

**No compiler in the image.** The torch builds the image carries route a
few operators through Triton kernels of their own, and the first such call
compiles Triton's CUDA launcher with the system C compiler — which would put
a compiler and the kernel headers it needs into a runtime image. The image
sets `TORCH_DISABLE_NATIVE_JIT=1` instead, so those operators run on
torch's precompiled kernels on every architecture, and nothing compiles or
loads new machine code at run time. The image
build checks that the switch still holds: it fails if torch registers any
JIT-compiled operator. Nothing else JIT-compiles by default: htrflow does
not call `torch.compile`, and ultralytics leaves it off.

The web and converter dockerfiles need none of this: every image they build
on is published for both architectures under its pinned digest, and neither
recipe names an architecture. The converter dockerfile is the web one's
last two stages on their own.

**Findings that do not apply.** Distroless Debian CVEs that Debian has not
fixed and nothing in the images can reach are recorded in
`.docker/distroless.openvex.json`, one statement per CVE, each scoped to the
exact package version so the next package update retires it; every Trivy
scan reads it with `--vex`. A test fails when the code starts to reach what a
statement calls unused. The file is for these images' scans only.

**Each architecture is built natively.** Nothing passes `--platform`:
`uv` crashes in a cross-architecture build, and a GPU image built for a
foreign architecture cannot be smoke-tested on the machine that built it.
`docker build` reads `TARGETARCH` from the host, and CI puts each
architecture on a runner of its own.

### Provenance and reproducibility

Every input is pinned ([CI → Dependency pins](ci.md#dependency-pins)):
images and the uv binary by digest, htrflow by commit, and every Python
package with hashes. The wrapper's own
dependencies and the leaf overrides come from the workspace lock
(`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build). The transformers line is a hashed requirements file per major,
`.docker/transformers/<major>.txt`, compiled from the `.in` file beside it
with `make transformers-requirements`: transformers, the `tokenizers` and
`huggingface-hub` it needs, `sentencepiece` and `protobuf`, installed with `--no-deps`
so nothing else in the base moves, and the build then checks that their
own requirements are met. A `TRANSFORMERS_VERSION` those files do not pin
fails the build. The htrflow base installs htrflow, torch and the rest of
its dependencies with `uv sync --locked` from the lock committed in
`.docker/htrflow-base/`: htrflow does not commit its own, and locking
afresh on every build meant two builds of one commit could differ.

The packages the images build from source — htrflow and the three workspace
members — need a build backend, and a lock pins what gets installed, not
what builds it. That backend and its dependencies are the hashed
`.docker/build-constraints.txt`, compiled from the `.in` file beside it with
`make build-constraints`. Every image builds its packages with `uv build
--build-constraints … --require-hashes`, which refuses a build requirement
that file does not pin and hash, and installs the dependencies with
`--no-build`, which refuses to build any of them from source. Nothing is
resolved at build time. Refresh that lock with `make lock-htrflow-base`
after moving `HTRFLOW_REF` or editing the overlay, and review the diff.

The build argument `HTRFLOW_BASE_REVISION` records which htrflow commit the
image runs (unset, `HTRFLOW_REF`; a local checkout passes its `git describe
--tags --always --dirty`). It is stamped as the OCI label
`se.riksarkivet.htrflow.base.revision` and into every ALTO file.

### Local builds

For fast iteration against a dev cluster's registry — no dagger, no push
credentials:

```bash
make poc-push                  # build-wrapper + build-web, push both to $(HTR_REGISTRY), print their digests
make build-wrapper             # just the wrapper image, for the host's architecture
make build-web                 # just the web image
make build-campaigns           # just the converter image
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

`make publish` runs exactly this for the wrapper — one unsigned image for
the host's architecture under the bare version tag, so releases go through
the publish workflow below instead. `--component` is `wrapper` (default),
`web` or `campaigns`. `publish-docker` refuses a tag that is already on the
registry (see below), runs the test suite and aborts on failure, builds, runs
the library-API pin test on the wrapper image it is about to push and
Trivy's CRITICAL gate on every component, and only then pushes and returns
the published reference with its digest.

**Tags are immutable.** Before its tests and again right before the push,
`publish-docker` asks the registry for the tag it will push and, with
`--tag-suffix`, for the bare tag as well. Only an answer of "no such
manifest" or "no such repository" counts as free: a registry it cannot get
an answer from refuses the publish rather than risk replacing a release.
`dagger call check-tag-free --image-repository <repo> --tag <tag>` runs the
check on its own.

**Tag resolution.** An explicit `--tag` must equal the version in
`packages/wrapper/pyproject.toml` (a leading `v` is ignored) unless
`--skip-validation` is set; an empty tag becomes `v<version>`. The images are
released as one set, so the web and converter images take the same tag. The
resolved tag is baked into every image as the `HTRFLOW_BATCH_VERSION` build
argument — kept
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
| campaigns | `riksarkivet/htrflow-campaigns` | `docker.io` |

Override with `--image-repository` and `--registry`. `--base-revision` sets
`HTRFLOW_BASE_REVISION` for the wrapper, and `--transformers-version` sets
`TRANSFORMERS_VERSION` (empty keeps the dockerfile's pin). Naming the other
transformers line publishes a tag of its own on that line, for models the
default line cannot read ([Model
handling](../how-it-works/wrapper.md#model-handling)).

### The publish workflow

`.github/workflows/publish.yml` is manual (`workflow_dispatch`) only, with
one required input, the tag (`v<version>`, equal to the wrapper's
`pyproject.toml` version), and one optional one, a transformers version,
which reaches every wrapper build as the flag above. Every job runs in
the `release` environment and reads the registry credentials
(`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`) from there; the dagger CLI is
installed before the registry login, from its release asset checked against
a committed checksum, and starts its engine by digest
(`.github/actions/setup-dagger`).

**What the `release` environment must carry** (repository settings →
Environments; a repository administrator sets it up once):

- **Required reviewers**, with *Prevent self-review*, so a run waits for a
  second maintainer before any job gets the credential. Every job waits;
  one reviewer can approve all pending ones at once.
- **Deployment branches and tags: selected branches**, `main` only, so a
  run dispatched on any other branch cannot reach the credential.
- **`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` as environment secrets**,
  then removed from the repository secrets, and the organisation secrets of
  the same name no longer shared with this repository — an environment
  secret wins over one of the same name, but the others would stay
  readable from every workflow.
- The token itself scoped on Docker Hub to the three repositories, with read
  and write only.

Until that is done the environment exists (the first run creates it) but
protects nothing, and the repository secrets keep the workflow running.

**Before a new component's first publish**, once on Docker Hub:

- create its repository **public** (Docker Hub makes new ones private);
- add it to the release token's scope — a token that cannot see the
  repository makes every publish job refuse, since the tag check gets no
  "no such repository" answer.

The workflow itself runs in three steps:

1. **Tags are immutable.** Every job first checks that neither its own
   per-architecture tag nor the final tag exists on the registry, and
   refuses to run if one does; `publish-docker` checks again itself. To fix
   a release, bump the version and publish a new tag.
2. **Through dagger, one job per image and architecture.** A matrix runs
   `publish-docker` on a runner of the architecture it is building for and
   pushes `<version>-<arch>`: every image, both architectures, with the
   same gates on each — the test suite, the library-API pin test on the
   wrapper, Trivy's CRITICAL gate.
3. **One multi-architecture tag per image.** A final job joins each image's
   pair into the manifest list `riksarkivet/htrflow-batch:<version>`,
   `riksarkivet/htrflow-web:<version>` and
   `riksarkivet/htrflow-campaigns:<version>` with `docker buildx imagetools
   create`, so a pull by tag or by the list's digest resolves to the node's
   architecture. **The digests the chart pins are these lists'**: pinning a
   per-architecture image instead is an image the other kind of node cannot
   pull. The release commit pins all three digests the manifest job
   printed:

   | Digest | Pinned in |
   |---|---|
   | `htrflow-web` | `web.image` in `charts/htrflow-batch/values.yaml`, and the compose stack |
   | `htrflow-batch` | the demo pipeline `init` writes, `packages/converter/src/htrflow_converter/template/pipelines/demo-v1.yaml`, and so its copy in `examples/campaigns/` |
   | `htrflow-campaigns` | all three containers of the Argo CD hook (the clone, the check and the apply), `packages/converter/src/htrflow_converter/template/argocd/apply.yaml`, with the release's tag as a comment beside it |

   `.dagger/published.go` (`publishedPins`) names the file each of the
   three is pinned in: the published-image checks (`scan-published`,
   `verify-published`) read the digest from there, so a pin that moves to
   another file moves there too.

   The hook digest moves with every release, since the hook runs flags of
   the converter that wrote it; a test fails CI when the hook's comment does
   not name the converter's own version.

   The same commit sets `CONVERTER_REF` in both CI templates `init --ci`
   writes,
   `packages/converter/src/htrflow_converter/ci/github/.github/workflows/render.yml`
   and `packages/converter/src/htrflow_converter/ci/azure/azure-pipelines.yml`,
   to a **full commit SHA**, with the tag as a comment beside it: the SHA of
   the release's version-bump commit (step 1 of
   [The GitHub release](#the-github-release)), which a tag, unlike a SHA,
   could later move off. Then `htrflow-campaigns init --force
   examples/campaigns` regenerates the example repository from both.

### Signing, SBOM and provenance

Every pushed digest goes through the composite action
`.github/actions/sign-attest`, shared by all three jobs so they cannot
drift:

- a **cosign** signature over the digest, keyless through Sigstore OIDC —
  what the chart's `security.verifyImages` verifies
  ([Chart values](../reference/chart.md));
- a **SLSA build-provenance** attestation, pushed to the registry;
- an **SPDX SBOM** generated by Trivy and attested, for every
  per-architecture image (a manifest list gets none of its own).

Verify a published image against the workflow identity:

```bash
# signature (cosign 3 or later; older versions report "no signatures found").
# Anchored to main, the only ref publishing runs from.
cosign verify docker.io/riksarkivet/htrflow-batch:<version> \
  --certificate-identity-regexp '^https://github\.com/AI-Riksarkivet/htrflow-batch/\.github/workflows/publish\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# build provenance
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<version> \
  -R AI-Riksarkivet/htrflow-batch

# SBOM, on a per-architecture image
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<version>-<arch> \
  -R AI-Riksarkivet/htrflow-batch --predicate-type https://spdx.dev/Document/<spdx-version>
```

## The GitHub release

Every `v*` tag gets a GitHub release from `.github/workflows/release.yml`. The
order is the one the images need:

1. **Bump and merge the version**: the wrapper's, the web's and the
   converter's `pyproject.toml`, and the chart's `appVersion`.
2. **Publish the images** for the tag with the publish workflow above.
3. **The release commit** pins the three manifest-list digests where the
   table above says (`charts/htrflow-batch/values.yaml`, the demo
   pipelines, the compose stack and the Argo CD hook), sets `CONVERTER_REF`
   in both CI templates to the SHA of step 1's commit, regenerates
   `examples/campaigns/`, and bumps the chart's `version` with a changelog
   entry.
4. **Tag that commit** and push the tag.

The workflow then writes the notes in two parts:

- **`.github/release-notes.md`**, the same for every release: the not-for-use
  warning, the three image digests (read from Docker Hub for the tag), how to
  install from the tag and how to verify the images. A tag whose images are
  not on Docker Hub fails the workflow instead of publishing notes that point
  at nothing.
- **The changes**, written by [git-cliff](https://git-cliff.org) from the
  conventional commits since the previous tag (`cliff.toml`): Added, Fixed,
  Changed, Build and CI, Documentation, each line led by its scope (`chart`,
  `converter`, `wrapper`, `web`, `frontend`). Slides, stories, specs,
  line budgets, formatting and tests are left out.

Below 1.0 every release is marked a pre-release. `make release-notes` shows
what the next release will list. git-cliff comes from `uv.lock`'s `release`
group, pinned and hash-checked like the docs tools.

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
installed and what is in git.
