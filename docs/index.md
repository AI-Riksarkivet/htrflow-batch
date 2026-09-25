# htrflow-batch

Run an [htrflow](https://github.com/AI-Riksarkivet/htrflow) pipeline on whole
archive volumes across a Kubernetes cluster of GPU nodes. Results stream to
an S3 bucket page by page and open in a IIIF viewer; what to transcribe is a
file in a git repository.

**[Quickstart: see it work in five minutes](getting-started/try-it.md)**, with
nothing but Docker.

!!! warning "Not for use yet"
    This project is under active development and is not ready for others to
    run: interfaces, chart values and the campaigns format still change
    without notice. This notice goes when there is a release to stand behind.

![One campaign, from a file in git to results in the viewer: git, delivery, the cluster, storage and the outside world](assets/diagrams/overview.svg)

## What it does

- **Runs htrflow unmodified**, as a library, page by page, so a long volume
  costs the same memory as a short one.
- **A campaign is a YAML file in git.** A converter renders it into one
  Kubernetes Indexed Job, one index per volume, plus a warm-up Job that
  caches the models. No CRD, no controller, no database.
- **Kueue owns queueing and GPU quota; Kyverno decides what may run**: only
  digest-pinned images from allowed registries, and optionally only
  revision-pinned models.
- **Every page is traceable.** Each ALTO file names the models, image and
  htrflow-batch build that produced it, and a volume is done only when
  every page is accounted for.
- **It measures itself.** Every volume records how long the GPU waited for
  page fetches, the number that decides whether a cache in front of the
  IIIF source is worth building ([Roadmap](roadmap/index.md)).

## Where to start

| You want to | Read |
|---|---|
| See it work | [Quickstart](getting-started/try-it.md) |
| Run it on your cluster | [Deploy](getting-started/deploy.md) → [Run a campaign](getting-started/campaigns.md) → [Troubleshooting](getting-started/troubleshooting.md) |
| Write campaigns | [Run a campaign](getting-started/campaigns.md) → [Campaign & Pipeline YAML](reference/campaign-yaml.md) → [View results](getting-started/viewing.md) |
| Change the code | [Development](development/index.md) → [How it Works](how-it-works/architecture.md) |

The [Reference](reference/index.md) holds the exact contracts. The
[Presentations](presentations.md) walk through all of it in pictures.

## Licence

EUPL-1.2, the same as htrflow (`LICENSE` at the repository root); what the
images ship is listed in [Third-party licences](development/licenses.md).
