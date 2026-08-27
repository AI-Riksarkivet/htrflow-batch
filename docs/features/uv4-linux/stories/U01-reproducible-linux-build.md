---
type: Product Backlog Item
id: 2900
parent: 2801
title: Build the UV4 fork on Linux, reproducibly, as a container image
---

# U01 · Build the UV4 fork on Linux, reproducibly, as a container image

**Story.** As the team, we want the Riksarkivet Universal Viewer to be
built from its git repository by a script on Linux, producing a container
image anyone can rebuild bit-for-bit, so that the viewer is no longer tied
to one developer's Windows machine.

## Why it matters

A viewer that only builds on one laptop cannot be deployed on a cluster,
cannot be updated by anyone else, and cannot be trusted to be the same
thing twice. This is the "UV4 runs on Linux" half of the feature.

## What this delivers

- A scripted build (Dagger `BuildViewer`, also `make viewer-image`) that
  clones the `Riksarkivet/universalviewer4` fork at a pinned commit, builds
  it with Node 20, and packages the result on a locked-down nginx image.
- Handles the Riksarkivet network's TLS interception (the CA bundle is
  passed in), which was the main reason the build failed on RA Linux hosts.
- The same image also serves the campaign browser (B05) at its root, so one
  deployment covers both.

## Done when

- [ ] `dagger call build-viewer` produces the image on a clean Linux
      machine and in CI.
- [ ] The image runs as non-root on port 8080 and passes the chart's
      restricted pod policy.
- [ ] The upstream commit it is built from is pinned and documented.
