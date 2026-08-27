---
type: Product Backlog Item
id: 2859
parent: 2800
title: Audit fixes — the wrapper never loses a page or misjudges a failure
---

# B30 · Audit fixes — the wrapper never loses a page or misjudges a failure

**Story.** As an archivist relying on the results, I want a transient
network problem to be retried rather than recorded as a permanent
failure, and a resumed volume to be exactly as complete as a fresh one —
so that "complete" in the status page always means every page, and
"failed" always means something a human must look at.

## What was found (audit package A2)

- **X7 (high)** — a temporary failure fetching the IIIF manifest was
  classified as *permanent* (no retry), and on resume a page's PAGE-XML
  could be silently dropped while its ALTO was kept.
- **X5 (high)** — the wrapper's exit codes and the Job contract the docs
  described did not match; node disruptions burned retry attempts; very
  long volumes could not finish within the fixed deadline.
- **X16 (medium)** — image builds were not fully reproducible (unpinned
  base images and dependencies).
- Test gaps at the boundaries (X19): stop events, in-flight accounting.

## What was done

- Transient vs permanent classification corrected for every fetch path;
  resume requires *both* PAGE and ALTO per page and compares page sources.
- Graceful SIGTERM: finish the page in flight, ship the log, exit with a
  code the reconciler recognises as "was making progress" so disruptions
  do not cost an attempt; page-count-derived deadline.
- Byte caps on manifest and page downloads (configurable, rendered by the
  chart); URL redaction in every log path.
- Base images, `uv` and torch pinned by digest/version; the base
  `htrflow` revision stamped into the image label.
- Sleeps in tests replaced with injected clocks and events.

## Done when

- [ ] Transient manifest/page failures lead to a retry, permanent ones do
      not (tested per path).
- [ ] A kill-and-resume produces a manifest identical to an uninterrupted
      run (verified on the PoC).
- [ ] Exit-code table in the docs matches the code (contract test, B21).
