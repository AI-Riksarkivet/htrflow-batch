---
type: Feature
id: 2801
parent: 2769
title: UV4 linux
---

# Feature: UV4 linux (#2801)

## In one paragraph

Riksarkivet maintains its own version of **Universal Viewer 4** (UV4), the
web viewer that shows a page image with the transcribed text beside it and
the text lines outlined on the image. Until now it was built and run on a
developer's Windows machine. This feature makes it **build and run on
Linux** — in a container, reproducibly, from a script — and makes it **open
transcriptions straight from S3**, so the results of the batch feature are
one click away for anyone with a browser.

## Why we are building it

- **Results people can look at.** A transcribed volume is only useful if a
  human can read it against the original. UV4 is that reading room.
- **Runs where the results are.** Building the viewer on Linux means it is
  deployed on the same cluster as the batch system, from the same Helm
  chart, and points at the same S3 bucket. No file copying, no separate
  server.
- **Reproducible.** The Linux build is scripted from the fork's git
  repository, so anyone can rebuild the exact same viewer image.

## What "done" looks like for the feature

The feature description in Azure says: *"make sure UV4 runs on Linux and can
show transcriptions from S3"*. Both are delivered on the proof-of-concept
node. The open stories are about making that robust: keeping our changes in
step with the upstream viewer, putting it behind a real web address rather
than an ssh tunnel, and testing it automatically.

## Stories

### Implemented in the repository — awaiting acceptance

| Id | Story |
|---|---|
| [U01](stories/U01-reproducible-linux-build.md) | Build the UV4 fork on Linux, reproducibly, as a container image |
| [U02](stories/U02-alto-text-panel-and-overlays.md) | Show the transcription next to the page, with lines outlined on the image |
| [U03](stories/U03-view-results-from-s3.md) | Open any batch-transcribed volume from S3 in the viewer |
| [U08](stories/U08-viewer-image-slsa-trivy.md) | Viewer image (UV4 + campaign browser) — CI build, SLSA provenance, SBOM and Trivy scan |

### Not started

| Id | Story |
|---|---|
| [U04](stories/U04-upstream-the-patch.md) | Get our viewer changes into the Riksarkivet fork |
| [U05](stories/U05-viewer-behind-ingress.md) | Give the viewer a real web address |
| [U06](stories/U06-search-inside-a-volume.md) | Search inside a volume from the viewer |
| [U07](stories/U07-viewer-smoke-test.md) | Automatically check that the viewer still works after every change |
| [U09](stories/U09-viewer-flow-diagram.md) | Viewer flow diagram |

U06 is expected to be superseded by the Search feature's S08 (IIIF Content
Search backed by the index). Story ids are stable
identifiers, never renumbered.
