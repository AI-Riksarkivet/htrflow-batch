---
type: Product Backlog Item
id: 2874
parent: 2800
title: Models as signed OCI artifacts in our registry (ModelPack)
---

# B35 · Models as signed OCI artifacts in our registry (ModelPack)

**Story.** As the security owner, I want the HTR models — the
segmentation and text-recognition weights — stored in **our registry as
signed, digest-pinned OCI artifacts**, exactly like the container images,
and pulled from there rather than from the internet, so that the last
thing the cluster fetches from outside disappears, and "which weights
produced this transcription?" has the same verifiable answer as "which
image?".

## Why it matters

Today the models are the exception to everything else in the trust
boundary. A pipeline names a Hugging Face repository; a warm-up pod with
**internet access** downloads the weights into the model cache; the
weights are **pickled Python objects** (loading one executes code); and
the only guard is a pinned revision hash checked by the reconciler. The
warm-up pod is the single pod in the namespace allowed to reach the
internet — and it has to be allowed the *whole* internet, because Hugging
Face is served from a CDN with no fixed address to allow-list.

**ModelPack** (the CNCF model-spec, tooling `modctl`) packages a model as
an OCI artifact: the weight files become layers, the manifest carries the
model's name, format, licence and origin, and it lives in a registry.
That gives models everything images already have — a digest, a cosign
signature, provenance, RBAC, retention — and lets the warm-up pull from
the local registry (B36) with no internet egress at all. A 2026-06 spike
proved the loop end to end with a Riksarkivet YOLO model: build → push
to Harbor → pull in-cluster → extract → load offline, with the extracted
file's checksum equal to the layer digest.

For NIS2 this closes the supplier gap the images alone leave open (Art.
21(3): the vulnerabilities and practices of each supplier and product —
here, the model author's): a model is a supplier artifact that executes
on our hardware, and after this story it is admitted only if we packaged
and signed it.

## What this delivers

- **A model-packaging step**: for each model a pipeline may use, a
  reproducible job (CI, on request) that fetches the Hugging Face repo at
  a pinned revision, packages it with `modctl` into a ModelPack artifact
  annotated with its origin (repo, revision, licence), pushes it to the
  `models` project in our registry, and **signs the digest with cosign**
  under the CI identity — the same identity the Kyverno policy trusts.
- **Pipeline files reference models by registry digest**
  (`oci://<registry>/models/<name>@sha256:…`) instead of, or alongside,
  a Hugging Face repo + revision; the reconciler validates the reference
  against the image allow-list prefix like any image.
- **The warm-up job pulls from the registry**: `cosign verify` against
  the CI identity *before* extraction, then `modctl` extract into the
  model cache in the layout `htrflow` already reads — so `htrflow` itself
  is unchanged and batch jobs stay `HF_HUB_OFFLINE=1`.
- **Warm-up internet egress removed** from the network policy; the
  warm-up reaches the registry and nothing else.
- Provenance: the volume's completion marker already records image
  digest and pipeline hash; it now records the model digests too, so a
  transcription names its exact weights.

## Known limits (from the spike)

- Vulnerability scanners do not scan model artifacts (Trivy refuses the
  model media type), so Harbor gives signing, RBAC, digest pinning and
  retention for weights — not CVE scans. The mitigation is the packaging
  step being the only path in, and a preference for safetensors over
  pickle where the model author offers it.
- `modctl` emits the `vnd.cnai.model.*` media types rather than the
  `vnd.cncf.model.*` shown in older docs; Harbor classifies both as
  models. Pin the `modctl` version in the packaging job.
- Kyverno verifies *images* at pod admission; a model artifact is not a
  pod image, so its signature is checked by the warm-up job, not the
  admission webhook. Record that in the trust-boundary docs.

## Done when

- [ ] Every model used by a pipeline in the campaigns repo exists in the
      registry as a signed ModelPack artifact with origin annotations.
- [ ] A warm-up given an unsigned or tampered model artifact fails
      before extraction, and the reconciler reports it (tested).
- [ ] The warm-up job's network policy has no internet egress; a
      campaign runs end to end from registry-hosted models only.
- [ ] `manifest.json` for a completed volume records the model digests.
- [ ] Packaging, signing and verification are documented for the person
      adding the next model.

- [ ] The Security → Trust boundary table gains a ModelPack row and retires the warm-up internet-egress and model-revision rows.
