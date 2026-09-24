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
archival volumes, across a Kubernetes cluster of GPU nodes. Your pipeline
stays exactly as it is; htrflow-batch decides where each volume runs, streams
its pages in, and publishes ALTO and PAGE XML page by page. What to
transcribe is a file in git.

![One campaign, from a file in git to results in the viewer](docs/assets/diagrams/overview.svg)

## Quickstart

One page of a sample volume, transcribed on your own machine and opened in
the viewer. You need `git`, `make` and Docker with Compose; no cluster, no
GPU.

```bash
git clone https://github.com/AI-Riksarkivet/htrflow-batch && cd htrflow-batch
make compose-up
docker compose -f .docker/docker-compose.yml logs -f wrapper    # wait for "COMPLETE 1 pages"
```

Then open
<http://localhost:8080/uv.html#?manifest=http://localhost:19000/htr-results/demo-v1/mock-vol/iiif.json>,
and `make compose-down` when you are done. Ports taken, or anything else
unexpected: [Quickstart](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/try-it/).

## On a cluster

A campaign lists volumes and names a pipeline. You open a pull request, and
once it is merged an apply sends it to the cluster as one Kubernetes Indexed
Job, one pod per volume, queued by Kueue until its GPUs are free.

```yaml
# campaigns/kyrkobocker-1.yaml
pipeline: demo-v1
volumes:
  - id: loc-mal2459400
    manifest: https://…/manifest.json
  - id: loose-scans
    images:
      - https://…/scan-0001.jpg
```

![One pod per archival volume: wait for the models, then fetch, transcribe and upload page by page, then verify, publish and exit](docs/slides/assets/p1-pod.svg)

A web front shows every campaign and volume live, with each volume's run
log and the transcription in the viewer:

![The status page: one card per campaign, with its volumes, pages and problems](docs/slides/assets/part-1-status-page.png)

- [Deploy](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/deploy/): the production install.
- [Run a campaign](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/campaigns/): your first campaigns repo.
- [How it works](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/architecture/), and the [slides](https://ai-riksarkivet.github.io/htrflow-batch/presentations/) that walk through it in pictures.

## Developing

```bash
make install && make test     # uv workspace sync + the Python tests
make ci                       # every gate CI runs, through dagger
```

[Development](https://ai-riksarkivet.github.io/htrflow-batch/development/)
covers the repository layout, testing, CI, releasing and a dev cluster. The
documentation site is built from `docs/` (`make docs-serve` locally).

## License

htrflow-batch is licensed under the European Union Public Licence (EUPL-1.2),
the same licence as htrflow. See [`LICENSE`](LICENSE). The third-party
components the images ship are listed in
[Third-party licences](https://ai-riksarkivet.github.io/htrflow-batch/development/licenses/).
