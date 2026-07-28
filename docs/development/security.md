# Security

## Pod security posture (design intent — not yet wired into the chart)

- `runAsNonRoot`, `readOnlyRootFilesystem` (writes confined to workdir mounts).
- `automountServiceAccountToken: false` — job pods need zero k8s API access.
- NetworkPolicy: egress only to the IIIF origin, the S3 endpoint, and HF Hub
  (drop HF egress once models are PVC'd or baked into the image — see
  [Model handling](../how-it-works/wrapper.md#model-handling)).
- Secrets: `htr-batch-s3`, `hf-token` — mounted, never in env dumps or logs.

None of these are currently rendered by `charts/htrflow-batch` — they are the
settled design (§8 of the original design doc) but D14 (below) tracks turning
them into actual chart templates and testing them on a real cluster.

## D14 — pod security + egress NetworkPolicy (open)

Proposed, not confirmed: this is the open item that would turn the posture
above into enforced Kubernetes objects (`SecurityContext` on the Job template,
a `NetworkPolicy` scoping egress). See [Open Items](../roadmap/open-items.md)
for where this sits relative to the other unconfirmed decisions. Variant (c)
of the [Phase 2 cache layer](../roadmap/phase-2-cache.md) actually *tightens*
this posture further if adopted — GPU pods would talk only to S3, with all
IIIF traffic isolated to the index shim.

## RustFS PoC-creds caveat

The chart's optional `devStack.rustfs` component (`templates/devstack-rustfs.yaml`)
hardcodes `rustfsadmin` / `rustfsadmin` as both the RustFS server credentials
and the `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the generated S3
Secret. This is **intentional and PoC-only** — `devStack.rustfs` exists purely
to stand up a disposable in-cluster S3 for replaying the PoC without external
dependencies (see [Deploy](../getting-started/deploy.md)), and is off by
default. Never enable `devStack.rustfs` against a deployment that holds real
data; point `s3.existingSecret` at a real Secret with real credentials
instead. The same caveat covers the devStack in-cluster registry, which is
unauthenticated by design (PoC image-iteration convenience only).
