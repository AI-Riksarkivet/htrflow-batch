---
type: Product Backlog Item
id: 2872
parent: 2800
title: Model-packaging job image built in CI with SLSA provenance and a Trivy scan
---

# B43 · Model-packaging job image built in CI with SLSA provenance and a Trivy scan

**Story.** As the security owner, I want the image that packages and signs models (B35) to be built, attested, scanned and signed by CI itself, so that the tool that vouches for the models is vouched for the same way.

## Why it matters

B35 introduces a new image — `modctl` plus the packaging script. An image that signs other artifacts is a high-value target; it must not be the one exception to B37's rule.

## What this delivers

- A Dockerfile pinning the `modctl` version and base image by digest; added to the publish matrix with signature, provenance, SBOM and Trivy.
- The packaging job in CI runs from that published digest only.

## Done when

- [ ] The image appears in the B37 inventory with all columns filled.
- [ ] A model packaged by it carries provenance naming the image digest.
