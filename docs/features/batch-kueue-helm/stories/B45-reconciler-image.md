---
type: Product Backlog Item
id: 2863
parent: 2800
title: Reconciler image — CI build, SLSA provenance, SBOM and Trivy scan
---

# B45 · Reconciler image — CI build, SLSA provenance, SBOM and Trivy scan

**Story.** As the security owner, I want the reconciler image — the only pod with cluster credentials — to be built in CI with provenance, SBOM, scan and signature, so that the component that creates Jobs is itself beyond substitution.

## Why it matters

Covered by the publish workflow today (B09); tracked as its own row because it is the most privileged image in the namespace and deserves its own acceptance.

## What this delivers

- Built by `publish.yml` from `.docker/htrflow-reconciler.dockerfile` with pinned base image; immutable tag; cosign signature; SLSA provenance; SPDX SBOM; Trivy blocking on CRITICAL.

## Done when

- [ ] `cosign verify` and `gh attestation verify` succeed for the latest published tag.
- [ ] The image passes Kyverno `Enforce` on DEV.
