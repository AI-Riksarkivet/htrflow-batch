---
type: Product Backlog Item
id: 2870
parent: 2800
title: `htrflow` base image for arm64 published and pinned by digest
---

# B42 · `htrflow` base image for arm64 published and pinned by digest

**Story.** As the platform team, we want the `htrflow` base image the GPU wrapper builds on to be published to our registry from CI, pinned by digest, so that the chain of custody for the GPU wrapper starts at a published artifact rather than a build on a laptop.

## Why it matters

The GPU wrapper `FROM`s an `htrflow:v0.2.6-arm64` image that exists only on the PoC node (it needs gcc, sentencepiece and a transformers pin to build under arm64). No published base image means no reproducible wrapper build (B41).

## What this delivers

- The arm64 build recipe moved into the `htrflow` repository's publish workflow (or a dedicated job here if upstream declines), producing a digest-pinned image in our registry.
- The wrapper Dockerfile's `FROM` changed to that digest; `HTRFLOW_BASE_REVISION` stamped from the published image label rather than a local checkout.
- Only needed while the target clusters are arm64 — recorded so the story is closed, not silently dropped, if DEV/prod are amd64.

## Done when

- [ ] The base image is pullable from the registry by digest and its build is reproducible from a tagged `htrflow` commit.
- [ ] B41 builds from it in CI.
