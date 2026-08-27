---
type: Product Backlog Item
id: 2862
parent: 2800
title: Wrapper (CPU) image — CI build, SLSA provenance, SBOM and Trivy scan
---

# B44 · Wrapper (CPU) image — CI build, SLSA provenance, SBOM and Trivy scan

**Story.** As the security owner, I want the CPU wrapper image (`.docker/htrflow-batch.dockerfile`) — used for smoke tests and CPU pipelines — to be built in CI with provenance, SBOM, scan and signature, so that it is a fully accounted-for row in the image inventory.

## Why it matters

This is one of the three images the publish workflow already covers (B09); the story exists so the inventory (B37) has one row per image with its own acceptance, and so that a future change to this image has a place to be tracked.

## What this delivers

- Built by `publish.yml` from pinned base image, `uv` and torch versions; immutable tag; cosign keyless signature; SLSA build-provenance attestation; SPDX SBOM; Trivy blocking on CRITICAL.

## Done when

- [ ] `cosign verify` and `gh attestation verify` succeed against the workflow identity for the latest published tag.
- [ ] The image passes Kyverno `Enforce` on DEV.
