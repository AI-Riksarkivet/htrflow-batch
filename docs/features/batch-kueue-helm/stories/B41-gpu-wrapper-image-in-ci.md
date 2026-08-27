---
type: Product Backlog Item
id: 2871
parent: 2800
title: GPU wrapper image (arm64) built in CI with SLSA provenance and a Trivy scan
---

# B41 · GPU wrapper image (arm64) built in CI with SLSA provenance and a Trivy scan

**Story.** As the platform team, we want the GPU wrapper image — the one that actually runs the models — to come out of the CI publish pipeline like every other image, so that the most important image in the system is no longer the only one built by hand.

## Why it matters

Today `make poc-push` builds this image on the PoC node because its base image only exists locally (B42). It therefore has no provenance, no scan and no signature, and a cluster enforcing signatures (B13) refuses it.

## What this delivers

- The `.docker/htrflow-batch-gpu-arm64.dockerfile` build added to the publish matrix, on an arm64 runner (GitHub-hosted or self-hosted) or as a multi-arch build.
- Same treatment as the other images: immutable tag, cosign signature, SLSA build-provenance attestation, SPDX SBOM, Trivy blocking on CRITICAL.
- Pipeline files on DEV reference the CI-published digest.

## Done when

- [ ] A release publishes the GPU wrapper with signature, provenance and SBOM verifiable with `cosign verify` / `gh attestation verify`.
- [ ] A campaign on DEV runs on the CI-built GPU wrapper under Kyverno `Enforce`.
