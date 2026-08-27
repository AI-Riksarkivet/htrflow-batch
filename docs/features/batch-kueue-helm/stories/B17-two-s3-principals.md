---
type: Product Backlog Item
id: 2896
parent: 2800
title: Give jobs and the reconciler separate, minimal S3 credentials
---

# B17 · Give jobs and the reconciler separate, minimal S3 credentials

**Story.** As the security owner, I want a transcription job to be able to
write only its own volume's results, and the reconciler to write only the
status area, so that a bug or compromise in one job cannot touch anything
but its own output.

## Why it matters

Today every pod shares one S3 key. The anonymous side of the bucket is
already split (the public can read results but not the status area), but a
credentialed pod can write anywhere. The audit flagged this as the main
open item on the trust boundary; it is cheap to fix before there are many
results to protect.

## What this delivers

- Two S3 users/policies created at bucket setup: one scoped to
  `<pipeline>/<volume>/*` plus the run-log key, one scoped to `status/*`
  and `sources/*`.
- A second Kubernetes secret, with the job spec mounting the job-scoped one.

## Done when

- [ ] A job using its credentials cannot list or write another volume's
      prefix (tested).
- [ ] The reconciler cannot write into a volume prefix (tested).
- [ ] Documented in the Security page's trust-boundary section.
