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
- En bevisad publish-körning gör `compose-test` grön; därefter kopplas den in på main-push och compose-imagerna pinnas på digest. (revision 2026-09-07, X32)

## Done when

- [ ] `cosign verify` and `gh attestation verify` succeed against the workflow identity for the latest published tag.
- [x] Web-imagen byggs för båda arkitekturerna och publiceras som en manifest list under den rena taggen, precis som wrappern: en matrix-entry per arkitektur på en runner av samma arkitektur (ingen qemu), per-arch-taggarna `<tag>-amd64` / `<tag>-arm64` med cosign-signatur, SLSA build provenance och SPDX SBOM var för sig, och manifest-listan signerad utan egen SBOM. (revision 2026-09-14: v0.2.0:s web-image var amd64-only och gav ImagePullBackOff på arm64-noden; den digest som chartens `web.image` pinnar ska därför vara manifest-listans, satt i release-commiten.)
- [ ] The image passes Kyverno `Enforce` on DEV.
