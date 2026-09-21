**Not for use yet.** htrflow-batch is under active development: the campaigns format, the chart values and the API may still change between versions. This is a pre-release for trying it out, not for production. Read the upgrade notes in [`charts/htrflow-batch/README.md`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/@TAG@/charts/htrflow-batch/README.md) before moving between versions.

## Images

Both images are multi-architecture (amd64, arm64), signed with cosign (keyless, Sigstore), and carry SLSA build provenance and an SPDX SBOM per architecture.

| Image | Digest |
|---|---|
| `docker.io/riksarkivet/htrflow-batch:@TAG@` | `@WRAPPER_DIGEST@` |
| `docker.io/riksarkivet/htrflow-web:@TAG@` | `@WEB_DIGEST@` |

## Install

```bash
git clone --branch @TAG@ https://github.com/AI-Riksarkivet/htrflow-batch
helm upgrade --install htr htrflow-batch/charts/htrflow-batch -n <namespace> --create-namespace   # values: see Deploy in the docs
uvx --from "git+https://github.com/AI-Riksarkivet/htrflow-batch@@TAG@#subdirectory=packages/converter" htrflow-campaigns --help
```

[Deploy](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/deploy/) is the production-shaped install; [Try it](https://ai-riksarkivet.github.io/htrflow-batch/getting-started/try-it/) runs one page without a cluster.

## Verify the images

```bash
cosign verify docker.io/riksarkivet/htrflow-batch:@TAG@ \
  --certificate-identity-regexp '^https://github\.com/AI-Riksarkivet/htrflow-batch/\.github/workflows/publish\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
gh attestation verify oci://docker.io/riksarkivet/htrflow-batch:@TAG@ -R AI-Riksarkivet/htrflow-batch
```

The same commands work for `htrflow-web`.

## Changes
