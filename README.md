# htrflow-batch

[![Tests](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/ci.yml/badge.svg)](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/ci.yml)
[![Documentation](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/docs.yml/badge.svg)](https://ai-riksarkivet.github.io/htrflow-batch/)
[![Publish](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/publish.yml/badge.svg)](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/publish.yml)
[![Docker](https://img.shields.io/docker/v/riksarkivet/htrflow-batch?sort=semver&label=docker)](https://hub.docker.com/r/riksarkivet/htrflow-batch)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![License EUPL-1.2](https://img.shields.io/badge/license-EUPL--1.2-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Built on htrflow](https://img.shields.io/badge/built%20on-htrflow-8A2BE2.svg)](https://github.com/AI-Riksarkivet/htrflow)

[![Security](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/security.yml/badge.svg)](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/security.yml)
[![CodeQL](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/codeql.yml/badge.svg)](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/codeql.yml)
[![Secret Leaks](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/trufflehog.yml/badge.svg)](https://github.com/AI-Riksarkivet/htrflow-batch/actions/workflows/trufflehog.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/AI-Riksarkivet/htrflow-batch/badge)](https://scorecard.dev/viewer/?uri=github.com/AI-Riksarkivet/htrflow-batch)

[![Signed with Sigstore](https://img.shields.io/badge/Sigstore-signed-purple.svg)](.github/actions/sign-attest/action.yml)
[![SLSA provenance](https://img.shields.io/badge/SLSA-provenance-blue.svg)](.github/actions/sign-attest/action.yml)
[![SBOM SPDX](https://img.shields.io/badge/SBOM-SPDX-green.svg)](.github/actions/sign-attest/action.yml)

> **Not for use yet.** This repository is under active development and is
> not ready for others to run: interfaces, chart values and the campaigns
> format still change without notice. The
> [releases](https://github.com/AI-Riksarkivet/htrflow-batch/releases) are
> pre-releases for trying it out, not for production.

Run an [htrflow](https://github.com/AI-Riksarkivet/htrflow) pipeline on whole
archival volumes, across a Kubernetes cluster of GPU nodes. Your pipeline stays
exactly as it is; htrflow-batch decides where each volume runs, streams its
pages in, and publishes ALTO and PAGE XML page by page.

**New here?** The [Distributed htrflow](https://ai-riksarkivet.github.io/htrflow-batch/presentations/)
slides walk through all of it in pictures.

## From one machine to many

| | one machine | many nodes |
|---|---|---|
| **where it runs** | your machine, its GPU | whichever node has a GPU free — chosen for you |
| **the pages** | a folder on its disk | fetched from a IIIF manifest or plain image URLs |
| **the models** | downloaded to that disk | a shared cache every node mounts |
| **the results** | a folder next to the pages | a bucket every node writes to and every browser reads |
| **when a machine fails** | you start again | the volume resumes on another node from the bucket |
| **how you start it** | a command | a file in git |

A *volume* here is an archival volume — a bound unit of pages with a reference
code such as `R0001203` — never a Kubernetes volume.

## A campaign is a file in git

```yaml
# campaigns/kyrkobocker-1.yaml
pipeline: demo-v1            # a pipeline file in the same repository
window: 4                    # optional: how many volumes run at once
priority: htr-bulk           # optional: where it goes in the line
volumes:
  - R0001203                 # a reference code is enough
  - id: loc-mal2459400
    manifest: https://…/manifest.json
  - id: loose-scans
    images:
      - https://…/scan-0001.jpg
```

You validate it locally, open a pull request, and once it is merged an apply
sends it to the cluster. Stopping, removing and restarting a campaign are git
changes too. There is no CRD, no controller and no database.

## How it fits together

![Rough architecture: git, delivery, the cluster, storage and the outside world](docs/slides/assets/part-1-architecture.svg)

- **A pure converter** (`htrflow-campaigns`) checks the campaign and renders it
  into one Kubernetes **Indexed Job**, one index per volume, plus a warm-up Job
  that fills the model cache. It runs in CI, never in the cluster.
- **Kyverno** decides which images and model revisions may run. **Kueue** holds
  a campaign until its GPUs are free, and lets higher priority go first.
- **A web front** shows every campaign and volume live, with each volume's run
  log and the transcription in the viewer.

- [Architecture](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/architecture/): the map, and the components and their boundaries
- [Campaigns](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/campaigns/): the campaigns repo, the converter and what it renders
- [Queueing](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/queueing/): how a campaign is admitted, paused and shared
- [Events and signals](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/signals/): what the system emits and who reads it
- [Failure handling](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/failure-handling/): retries, exit codes, what a person is told

## htrflow in a pod

![One pod per archival volume: wait for the models, then fetch, transcribe and upload page by page, then verify, publish and exit](docs/slides/assets/p1-pod.svg)

Every pod runs htrflow — your pipeline, unchanged — on one archival volume.
Each page is uploaded the moment it is done, with provenance in every ALTO:
which models, which image, which htrflow-batch. A restarted pod skips the pages
already in the bucket, so a crash costs one page, not a volume.

- [From image to transcription](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/page-flow/): this path in detail
- [The wrapper](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/wrapper/): stages, provenance, model cache, and why a long volume costs the same memory as a short one

## What you see

![The status page: one card per campaign, with its volumes, pages and problems](docs/slides/assets/part-1-status-page.png)

One card per campaign: how it stands, a bar per volume, and one sentence for
each volume that failed, saying why. A volume's name opens it in the viewer; its
page icon opens the run log.

## What is in the repository

| Path | What it is |
|---|---|
| `packages/wrapper` | The in-pod wrapper: IIIF fetch, streaming, resume, verify, publish, live run log, warm-up entrypoint, ALTO provenance |
| `packages/converter` | `htrflow-campaigns`: validate, render and apply a campaigns repo (Indexed Jobs, warm-up Jobs, ConfigMaps; pause via Kueue) |
| `packages/web` + `frontend` | The read API (`/api/v1/jobs`) and the SvelteKit status page, one image |
| `charts/htrflow-batch` | The Helm chart: Kueue queues, RBAC, NetworkPolicies, Kyverno policies, the status page |
| `charts/htrflow-devstack` | S3 (RustFS), an image registry and the NVIDIA device plugin for a disposable dev cluster |
| `examples/campaigns` | The shape of a campaigns repository, with the CI that renders, policy-checks and commits `rendered/` |
| `docs/` | The documentation site (getting started, how it works, reference, development, roadmap). `docs/features/` holds the product view: one story per deliverable, mirrored to Azure DevOps and kept out of the site |
| `scripts/` | The exact LOC budgets, the generated configuration reference, the docs lint, the stories ↔ Azure DevOps sync |

## From nothing to a first transcription

Two paths, both written out step by step in
[Try it](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/try-it/):

- **Without a cluster.** `make compose-smoke` runs the wrapper on one page
  of a fixture volume against a local S3 server and checks the viewer. It
  needs only Docker.
- **On a cluster with one NVIDIA GPU node.** Install the prerequisites the
  chart does not carry, then install the chart itself. Then describe your
  volumes in a campaigns repo and apply it. The published, signed images are
  used, so nothing has to be built.

  ```bash
  make install && make install-kueue && make install-devstack
  helm upgrade --install htr charts/htrflow-batch -n <namespace> --set …   # values: see Try it
  uv run htrflow-campaigns init my-campaigns                              # add your volumes
  make campaigns-apply DIR=my-campaigns
  ```

The dev-cluster path runs with the security policies off, which is the right
default for a first look.
[Deploy](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/deploy/)
is the production-shaped install, with your own S3 and the policies on.

## Developing

```bash
make install && make test              # uv workspace sync + wrapper, converter and web tests
make frontend-install && make frontend-test
make ci                                # the dagger gates CI runs: format, lint, typecheck, frontend, chart, tests
scripts/loc-budget.sh                  # the non-test line budgets, a CI step of its own
```

[Development](https://ai-riksarkivet.github.io/htrflow-batch/development/)
covers workspace setup, testing, CI and
[releasing](https://ai-riksarkivet.github.io/htrflow-batch/development/releasing/).
[Dev cluster](https://ai-riksarkivet.github.io/htrflow-batch/development/dev-cluster/)
is the loop of building images and applying campaigns on a single-node GPU
cluster.

## Where things stand

- **Images.** `docker.io/riksarkivet/htrflow-batch` (the wrapper) and
  `docker.io/riksarkivet/htrflow-web` (the web front) are signed with cosign
  and carry SLSA provenance and an SBOM. Pipeline files pin the wrapper by
  digest, and the chart's `web.image` pins the web front the same way.
- **Versions.** Each lives next to what it versions: the charts' `Chart.yaml`
  files, the packages' `pyproject.toml` files, and `KUEUE_VERSION` in the
  `Makefile`.
- **Plans.** What is open and what could come next is on the
  [Roadmap](https://ai-riksarkivet.github.io/htrflow-batch/roadmap/).

## Documentation

The site is at <https://ai-riksarkivet.github.io/htrflow-batch/>, built from
`docs/` on every merge to main, with the
[slides](https://ai-riksarkivet.github.io/htrflow-batch/presentations/) beside
it. Locally:

```bash
make docs-serve
```

## License

htrflow-batch is licensed under the European Union Public Licence (EUPL-1.2),
the same licence as htrflow. See [`LICENSE`](LICENSE). The third-party
components the images ship are listed with their licences in
[Third-party licences](https://ai-riksarkivet.github.io/htrflow-batch/development/licenses/).
