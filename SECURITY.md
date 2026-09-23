# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub's private vulnerability reporting](https://github.com/AI-Riksarkivet/htrflow-batch/security/advisories/new)
rather than opening a public issue.

Include what you did, what happened, and what you expected. A proof of concept helps but is
not required. We aim to acknowledge within a week.

## Scope

In scope: the wrapper, the campaign converter (`htrflow-campaigns`), the read API and
status page (the web image and the campaign browser), the Helm charts and the Kyverno
policies they ship, the container images, the dagger module and the release pipeline.

Not in scope here:

- **htrflow itself** — report those to
  [AI-Riksarkivet/htrflow](https://github.com/AI-Riksarkivet/htrflow).
- **Models, IIIF image servers and archival material** a campaign points at, and any
  campaigns repository you run yourself.

Some properties are by design and documented rather than bugs — above all that write access
to a campaigns repository is equivalent to cluster operator. The
[Security page](https://ai-riksarkivet.github.io/htrflow-batch/how-it-works/security/)
describes the trust boundary, the pod security posture and the NetworkPolicy the chart
enforces.

## What the automated checks already cover

- **Trivy** gates CRITICAL findings in all three images (the wrapper, the web image and
  the converter image the Argo CD hook runs): on every push to `main`
  (`.github/workflows/ci.yml`), before every image is pushed at release, on each
  architecture (`.github/workflows/publish.yml`), and every week, both rebuilt from `main`,
  with the report in the Security tab, and as the published digests the repository pins,
  pulled from the registry on both architectures (`.github/workflows/security.yml`).
- **Signatures** of those published digests are checked through the chart's own
  verify-images policy and the Kyverno CLI on every change and every week.
- **CodeQL** analyses the Python packages, the TypeScript campaign browser, the Go dagger
  module and the workflows (`.github/workflows/codeql.yml`).
- **TruffleHog** scans the full git history for leaked credentials on every push and pull
  request (`.github/workflows/trufflehog.yml`), alongside GitHub's own secret scanning.
- **Dependabot** security updates are enabled.
- **OpenSSF Scorecard** grades the repository's supply-chain posture weekly
  (`.github/workflows/scorecard.yml`).

Actions are pinned by commit SHA, images by digest, the dagger CLI by release checksum,
and Python and frontend dependencies by lockfile, with hashes. That includes the wrapper
image's htrflow base, which the wrapper dockerfile builds from htrflow's source at a pinned
commit against a lockfile committed here (`.docker/htrflow-base/`, torch included), and the
packages the wrapper image adds on top, installed from `uv.lock` or compiled requirement
files.

Publishing needs the Docker Hub credential. Only the jobs of `publish.yml` use it, all in
the `release` environment, which is where its reviewers, its branch rule and the credential
itself belong ([Releasing](https://ai-riksarkivet.github.io/htrflow-batch/development/releasing/));
`publish-docker` refuses to push a tag that is already on the registry.

## Verifying a release

Every image `publish.yml` pushes is signed keylessly with cosign and carries a SLSA
build-provenance attestation; the per-architecture wrapper images and the web image also
carry an SPDX SBOM attestation (the multi-architecture index does not — an SBOM of an index
would describe only one architecture).

```bash
# Signature. Needs cosign 3 or later: older versions report "no signatures found".
# The identity is anchored to the branch publishing runs from: an unanchored
# `publish\.yml@` would also accept a signature made from any other ref.
cosign verify docker.io/riksarkivet/htrflow-batch:<tag> \
  --certificate-identity-regexp '^https://github\.com/AI-Riksarkivet/htrflow-batch/\.github/workflows/publish\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# Build provenance
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<tag> \
  -R AI-Riksarkivet/htrflow-batch

# SBOM, on a per-architecture image
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:<tag>-<arch> \
  -R AI-Riksarkivet/htrflow-batch --predicate-type https://spdx.dev/Document/v2.3
```

The chart can enforce the signature at admission. `security.verifyImages` is off by
default; with `enabled: true`, `issuer: https://token.actions.githubusercontent.com`, a
`subject` naming this repository's `publish.yml` on `refs/heads/main` and the
`imageReferences` to check, Kyverno refuses an image whose signature does not verify.
`charts/htrflow-batch/values-prod.yaml` sets all of it.
