---
type: Product Backlog Item
id: 2869
parent: 2800
title: Image inventory — no image runs that CI did not build
---

# B37 · Image inventory — no image runs that CI did not build

**Story.** As the security owner, I want a maintained list of every container image the batch system can run, each pointing at the story that gives it a reproducible CI build, SLSA provenance, SBOM, Trivy scan and signature — and a rule, enforced by the image allow-list and Kyverno, that nothing outside that list runs — so that "hand-built on someone's machine" is not a category that exists.

## Why it matters

Every control that says "only our images run" (B13, B36) is only as good as the list of what "our images" are. Today that list is implicit, and one image — the arm64 GPU wrapper — is built by hand and would be refused by the policy. One story per image (B41–B45, U08) does the work; this story is the list and the rule.

## What this delivers

- A table in the Security docs: image · Dockerfile · base image · where built · provenance/SBOM/scan/signature story · status.
- The rule written into the trust-boundary docs and enforced by `security.allowedImageRepos` + Kyverno `Enforce`: an image not in the table cannot be admitted.
- `make poc-push` marked PoC-only and refused on DEV/staging/prod by the allow-list.

## Done when

- [ ] The table exists, names every image the chart or a pipeline can reference, and each row links to a story.
- [ ] Kyverno `Enforce` on DEV admits every image in the table and refuses one that is not.
