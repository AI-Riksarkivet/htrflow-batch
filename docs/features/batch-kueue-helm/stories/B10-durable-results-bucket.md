---
type: Product Backlog Item
id: 2841
parent: 2800
title: Store results on the HCP
---

# B10 · Store results on the HCP

**Story.** As the owner of the transcription results, I want them written
to Riksarkivet's **Hitachi Content Platform (HCP)** object store — the
storage the organisation already operates, backs up and retains — so that a
campaign's output is an archive asset from the first page, not something on
a test node's disk that we would have to re-run.

## Why it matters

Everything the batch system produces — transcriptions, provenance, status —
lives in S3-compatible storage and nowhere else. On the proof-of-concept
node that storage is a single unreplicated disk (RustFS on one PVC). The
HCP is the decided target; this story is the move. It is the one
prerequisite that blocks the dev cluster (B12) and the archive-scale run
(B16), because both must write somewhere that will still exist next year.

## What this delivers

- An HCP **namespace/bucket** for HTR results, with an owner, a retention
  and backup arrangement written down, and S3 credentials issued for the
  batch system (later split in two, B17).
- The chart configured for it: `S3_ENDPOINT` = the HCP's S3 endpoint,
  bucket name, credentials secret; the `publicResultsBase` the viewer uses
  pointing at the HCP's browser-reachable address.
- **HCP S3-compatibility verified for what the system relies on**, each
  checked and recorded, not assumed:
    - anonymous read on the results prefix (`<pipeline>/<volume>/*`) for
      the viewer and browser, with the `status/` tree credential-only —
      the bucket-policy split from B31. If HCP's policy model cannot
      express it, the fallback is serving results through the viewer's
      nginx as a read-only proxy;
    - CORS headers for the viewer's origin;
    - the listing, HEAD and multipart-upload calls the wrapper and
      reconciler make (`boto3`), and the wrapper's write-last completion
      marker semantics (`manifest.json` visible only after all pages).
- A known volume replayed into the HCP and opened in the viewer.
- The RustFS dev stack kept for local development only, documented as such.

## Done when

- [ ] HCP namespace exists with owner, retention and backup named.
- [ ] Every compatibility check above passes, with results in the docs
      (S3 Layout page); any fallback chosen is implemented.
- [ ] A test campaign runs through the reconciler into the HCP; volumes
      complete and open in the viewer from the HCP address.
- [ ] Network path from the cluster to the HCP endpoint is allowed in the
      chart's egress policy.
