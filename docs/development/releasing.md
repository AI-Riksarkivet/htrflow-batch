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
  source checkout with this repository's `.docker/htrflow-base.dockerfile`
  and the lockfile committed beside it, and passed in as a build argument,
  plus what that base lacks at runtime: a C compiler and Python headers
  (triton JIT-compiles a CPython extension on the GPU path). The wrapper
  dockerfile checks the torch and torchvision versions that base carries,
  so a refreshed base lock that moves them fails the build instead of
  changing the image silently. Building that base is described in
  [Dev cluster](dev-cluster.md#the-gpu-wrapper-image).

Every architecture-specific step sits behind a `TARGETARCH` test. The
transformers line is not one of them: both architectures install it from the
`TRANSFORMERS_VERSION` build argument, whose default is the line upstream
htrflow is tested on ([Two transformers
lines](../how-it-works/wrapper.md#model-handling)).

The web dockerfile needs none of this. Every image it builds on — the two
Node toolchains, the Debian build stage and the distroless runtime — is
published for both architectures under the digest it is pinned to, and
nothing in the recipe names an architecture, so the same dockerfile produces
either image with no branch in it.

**Each architecture is built natively.** Nothing passes `--platform`:
`uv` crashes in a cross-architecture build, and a GPU image built for a
foreign architecture cannot be smoke-tested on the machine that built it.
`docker build` reads `TARGETARCH` from the host, and CI puts each
architecture on a runner of its own.

### Provenance and reproducibility

Every input is pinned ([CI → Dependency pins](ci.md#dependency-pins)): base
images and the uv binary by digest, torch and torchvision by version per
base, and everything else Python with hashes. The wrapper's own
dependencies and the leaf overrides come from the workspace lock
(`uv export --locked … --require-hashes`, so a stale `uv.lock` fails the
build). The transformers line is a hashed requirements file per major,
`.docker/transformers/<major>.txt`, compiled from the `.in` file beside it
with `make transformers-requirements`: transformers, the `tokenizers` and
`huggingface-hub` it needs, `sentencepiece` and `protobuf`, installed with `--no-deps`
so nothing else in the base moves, and the build then checks that their
own requirements are met. A `TRANSFORMERS_VERSION` those files do not pin
fails the build. Nothing is resolved at build time. The source-built base installs htrflow's
dependencies with `uv sync --locked` from the lockfile committed in
`.docker/htrflow-base/`: htrflow does not commit its own, and locking
afresh on every build meant two builds of one commit could differ. The
only unlocked Python install left is the torch swap from the CUDA wheel
index on the upstream base.

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

`make publish` runs exactly this for the wrapper — one unsigned image for
the host's architecture under the bare version tag, so releases go through
the publish workflow below instead. `--component` is `wrapper` (default) or
`web`. `publish-docker` refuses a tag that is already on the registry (see
below), runs the test suite and aborts on failure, builds, runs the
library-API pin test on the wrapper image it is about to push and Trivy's
CRITICAL gate on either image, and only then pushes and returns the
published reference with its digest.

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
`TRANSFORMERS_VERSION`: empty, the default, keeps the dockerfile's pin.
**That pin is unconditional and it is new.** The architecture whose base is
the upstream image used to keep whatever transformers version that base
carried; now both architectures install the pinned one, so the next release
moves it — deliberately, onto the line the other architecture has been
running. Nothing in CI builds the wrapper image, so the publish workflow is
where that build is first proven; treat the first release after this change
as one to watch. Naming the other transformers line publishes that tag on it
instead — for models the default
line cannot read ([Two transformers
lines](../how-it-works/wrapper.md#model-handling)) — and, since one run
publishes one image, that is a tag of its own, not a second variant of an
existing one.

### The publish workflow

`.github/workflows/publish.yml` is manual (`workflow_dispatch`) only, with
one required input, the tag (`v<version>`, equal to the wrapper's
`pyproject.toml` version), and two optional ones: a base revision and a
transformers version, which reaches the dagger-built architecture as the
flag above and the other as a `docker build` argument. Every job runs in
the `release` environment and reads the registry credentials
(`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`) from there; the dagger CLI is
installed before the registry login, from its release asset checked against
a committed checksum, and starts its engine by digest
(`.github/actions/setup-dagger`).

**What the `release` environment must carry** (repository settings →
Environments; a repository administrator sets it up once):

- **Required reviewers**, with *Prevent self-review*, so a run waits for a
  second maintainer before any job gets the credential. The three jobs
  each wait; one reviewer can approve all pending ones at once.
- **Deployment branches and tags: selected branches**, `main` only, so a
  run dispatched on any other branch cannot reach the credential.
- **`DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` as environment secrets**,
  then removed from the repository secrets, and the organisation secrets of
  the same name no longer shared with this repository — an environment
  secret wins over one of the same name, but the others would stay
  readable from every workflow.
- The token itself scoped on Docker Hub to the two repositories, with read
  and write only.

Until that is done the environment exists (the first run creates it) but
protects nothing, and the repository secrets keep the workflow running.

1. **Tags are immutable.** Every job first checks that neither its own
   per-architecture tag nor the final tag exists on the registry, and
   refuses to run if one does; `publish-docker` checks again itself. To fix
   a release, bump the version and publish a new tag.
2. **Through dagger, one job per image and architecture.** A matrix runs
   `publish-docker` on a runner of the architecture it is building for and
   pushes `<version>-<arch>`: the wrapper for one architecture, the web
   image for both. The web image needs nothing more than that second matrix
   entry — it has no base image to prepare and no architecture-specific
   step.
3. **The wrapper for the other architecture, natively.** A second job on a
   runner of that architecture builds the htrflow base from source at the
   pinned htrflow commit, runs `dagger call test` (the same gate
   `publish-docker` applies), then builds the wrapper with plain
   `docker build`, runs the library-API pin test and the Trivy CRITICAL
   gate on that image (`make test-driver-real`, `make scan-image`) and only
   then pushes `<version>-<arch>`. It is not dagger because
   the dagger engine builds in its own cache and cannot see a base image
   that exists only in the runner's docker daemon.
4. **One multi-architecture tag per image.** A final job joins each image's
   pair into the manifest list `riksarkivet/htrflow-batch:<version>` and
   `riksarkivet/htrflow-web:<version>` with `docker buildx imagetools
   create`, so a pull by tag or by the list's digest resolves to the node's
   architecture. **The digests the chart pins are these lists'**: pinning a
   per-architecture image instead is an image the other kind of node cannot
   pull. The release commit sets `wrapper.image` and `web.image` in
   `charts/htrflow-batch/values.yaml` to the two digests the manifest job
   printed, and campaign pipelines take the wrapper's.

### Signing, SBOM and provenance

Every pushed digest goes through the composite action
`.github/actions/sign-attest`, shared by all three jobs so they cannot
drift:

- a **cosign** signature over the digest, keyless through Sigstore OIDC —
  what the chart's `security.verifyImages` verifies
  ([Chart values](../reference/chart.md));
- a **SLSA build-provenance** attestation, pushed to the registry;
- an **SPDX SBOM** generated by Trivy and attested, for every
  per-architecture image. A manifest list gets no SBOM of its own; its member
  images carry the package lists, and an SBOM of the list would only describe
  whichever architecture the runner that made it happened to be.

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

## The GitHub release

Every `v*` tag gets a GitHub release from `.github/workflows/release.yml`. The
order is the one the images need:

1. **Bump and merge the version**: the wrapper's, the web's and the
   converter's `pyproject.toml`, and the chart's `appVersion`.
2. **Publish the images** for the tag with the publish workflow above.
3. **The release commit** pins the two manifest-list digests in
   `charts/htrflow-batch/values.yaml`, the demo pipelines and the compose
   stack, and bumps the chart's `version` with a changelog entry.
4. **Tag that commit** and push the tag.

The workflow then writes the notes in two parts:

- **`.github/release-notes.md`**, the same for every release: the not-for-use
  warning, the two image digests (read from Docker Hub for the tag), how to
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
installed and what is in git. `Chart.yaml` has no `icon` (`helm lint` calls
it recommended); fill it in before publishing a chart.
