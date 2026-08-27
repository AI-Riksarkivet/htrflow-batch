---
type: Product Backlog Item
id: 2903
parent: 2801
title: Viewer image (UV4 + campaign browser) — CI build, SLSA provenance, SBOM and Trivy scan
---

# U08 · Viewer image (UV4 + campaign browser) — CI build, SLSA provenance, SBOM and Trivy scan

**Story.** As the security owner, I want the viewer image — the one that faces every browser — built in CI with provenance, SBOM, scan and signature, so that what people open in their browser is as accounted for as what runs on the GPU.

## Why it matters

U01 made the build reproducible; B09's publish workflow signs and attests it. This story is the viewer's row in the image inventory (B37), with its own acceptance, and the place to track the next change to it.

## What this delivers

- Built by `publish.yml` via Dagger `BuildViewer` (pinned UV4 commit + patch, node:20, bun build of the SPA) onto a digest-pinned `nginx-unprivileged`; immutable tag; cosign signature; SLSA provenance; SPDX SBOM; Trivy blocking on CRITICAL.

## Done when

- [ ] `cosign verify` and `gh attestation verify` succeed for the latest published tag.
- [ ] The image passes Kyverno `Enforce` on DEV.
