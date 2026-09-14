---
type: Product Backlog Item
id: 2874
parent: 2800
title: Models as signed OCI artifacts in our registry (ModelPack)
---

# B35 · Models as signed OCI artifacts in our registry (ModelPack)

**Story.** As the security owner, I want the HTR models — the
segmentation and text-recognition weights — stored in **our registry as
signed, pinned OCI artifacts**, exactly like the container images,
and pulled from there rather than from the internet, so that the last
thing the cluster fetches from outside disappears, and "which weights
produced this transcription?" has the same verifiable answer as "which
image?".

## Why it matters

Today the models are the exception to everything else in the trust
boundary. A pipeline names a Hugging Face repository; a warm-up pod with
**internet access** downloads the weights into the model cache; the
weights are **pickled Python objects** (loading one executes code); and
the only guard is a pinned revision hash checked at admission by a Kyverno
policy. The warm-up pod is the single pod in the namespace allowed to
reach the internet — and it has to be allowed the *whole* internet,
because Hugging Face is served from a CDN with no fixed address to
allow-list.

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

## How a model is pinned: immutable tags

Product owner ruling, 2026-09-14: **models are pinned with immutable tags
in Harbor, for now.** `modctl` cannot pull an artifact by digest (see
Known limits), so the registry enforces the pin instead:

- The `models` project carries a **tag immutability rule** covering every
  repository and every tag. An immutable tag can be neither moved to a
  different manifest nor deleted; Harbor answers both with HTTP 412.
- A model's tag is its **source revision** — the pinned Hugging Face
  commit — so a tag names one exact set of weights for as long as the
  registry exists.
- The **digest is still recorded and checked**, because a tag alone
  cannot be verified by the consumer: the packaging job records the
  digest it pushed, pipeline files carry it next to the tag, and the
  warm-up compares the two before it extracts anything.

## What this delivers

- **The `models` project in Harbor has a tag immutability rule**
  (`**` repositories, `**` tags), configured as code with the registry,
  not by hand.
- **A model-packaging step**: for each model a pipeline may use, a
  reproducible job (CI, on request) that fetches the Hugging Face repo at
  a pinned revision, packages it with `modctl` (`--source-url`,
  `--source-revision`, `--no-creation-time`) into a ModelPack artifact
  annotated with its origin (repo, revision, licence), pushes it to the
  `models` project under the tag `<source revision>`, and **signs the
  digest with cosign** under the CI identity — the same identity the
  Kyverno policy trusts.
- **The packaging step verifies its own push**: after `modctl push` it
  resolves the tag in the registry and fails unless the tag's manifest
  digest equals the digest it built. `modctl`'s exit code alone is not
  enough (see Known limits).
- **Pipeline files reference a model by immutable tag and expected
  digest** (`<registry>/models/<name>:<source revision>` plus
  `sha256:…`), instead of a Hugging Face repo + revision; the reference is
  validated against the allowed registry prefix like any image.
- **The warm-up job pulls from the registry**: resolve the tag and fail
  if its digest differs from the pipeline file's, `cosign verify` the
  digest against the CI identity, then `modctl pull` by tag and extract
  into the model cache in the layout `htrflow` already reads — so
  `htrflow` itself is unchanged and batch jobs stay `HF_HUB_OFFLINE=1`.
  Because the tag is immutable, the manifest the warm-up checks is the
  manifest `modctl` pulls.
- **Warm-up internet egress removed** from the network policy; the
  warm-up reaches the registry and nothing else.
- Provenance: the volume's completion marker already records image
  digest and pipeline hash; it now records the model digests too, so a
  transcription names its exact weights.

## Known limits

From the 2026-06 spike:

- Vulnerability scanners do not scan model artifacts (Trivy refuses the
  model media type), so Harbor gives signing, RBAC, digest pinning and
  retention for weights — not CVE scans. The mitigation is the packaging
  step being the only path in, and a preference for safetensors over
  pickle where the model author offers it.
- `modctl` emits the `vnd.cnai.model.*` media types rather than the
  `vnd.cncf.model.*` shown in the current model-spec; Harbor classifies
  both as models. Pin the `modctl` version in the packaging job.
- Kyverno verifies *images* at pod admission; a model artifact is not a
  pod image, so its signature is checked by the warm-up job, not the
  admission webhook. Record that in the trust-boundary docs.

From the 2026-09-14 spike (`modctl` 0.2.2, Harbor 2.15, containerd 2.2,
Model CSI Driver 0.1.2), each verified on the dev cluster:

- **`modctl` cannot pull by digest.** `repo@sha256:…` fails with
  `failed to fetch the manifest: invalid reference` on `pull` and
  `fetch`; `pkg/backend/pull.go` fetches the manifest by `ref.Tag()`
  only. `repo:tag@sha256:…` succeeds **even with a wrong digest** — the
  digest is silently ignored. Never rely on a digest in a `modctl`
  reference; this is why the pin lives in the registry.
- **`modctl push` can report success when Harbor refused the tag.**
  Pushing different content to an existing immutable tag exits 0: the
  tag PUT gets 412, `modctl` retries, finds the tag already exists and
  skips it without comparing digests, leaving an untagged manifest
  behind. Hence the packaging step's post-push digest check.
- Layers are checked against the manifest's digests only after they are
  written to the extraction directory; nothing checks the manifest
  against a requested digest. The warm-up's tag-to-digest comparison
  covers that gap.
- **ModelPack cannot be mounted as a Kubernetes image volume on
  containerd.** The pull succeeds and the volume mounts as an empty
  directory with no event; containerd unpacks only standard image layers
  (containerd issue 11381 is open). CRI-O can mount OCI artifacts. A
  plain OCI image (`FROM scratch` plus the model files) does mount on
  containerd and loads in `htrflow`.
- **How models reach the pods is still open.** Warm-up extraction into
  the model cache (above) keeps the `ReadWriteOnce` cache and its
  one-node limit. The Model CSI Driver mounts ModelPack per pod and loaded
  the model, but it resolves tags only, re-downloads the full model for
  every pod (the per-volume directory is deleted on unmount), disables
  TLS verification for registry pulls, has no signature verification and
  runs as a privileged DaemonSet.

## Done when

- [ ] The `models` project's tag immutability rule is configured as code;
      moving or deleting a tag in it is refused (tested).
- [ ] Every model used by a pipeline in the campaigns repo exists in the
      registry as a signed ModelPack artifact with origin annotations,
      tagged with its source revision.
- [ ] The packaging step fails when the pushed tag does not resolve to
      the digest it built (tested by pushing different content to an
      existing tag).
- [ ] A warm-up given an unsigned or tampered model artifact, or a tag
      whose digest differs from the pipeline file's, fails before
      extraction, and the campaign page reports it (tested).
- [ ] The warm-up job's network policy has no internet egress; a
      campaign runs end to end from registry-hosted models only.
- [ ] `manifest.json` for a completed volume records the model digests.
- [ ] Packaging, signing and verification are documented for the person
      adding the next model.
- [ ] The Security → Trust boundary table gains a ModelPack row and
      retires the warm-up internet-egress and model-revision rows.
