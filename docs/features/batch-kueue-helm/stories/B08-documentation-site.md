---
type: Product Backlog Item
id: 2849
parent: 2800
title: Documentation an operator can deploy and run from
---

# B08 · Documentation an operator can deploy and run from

**Story.** As a new team member or platform operator, I want to deploy,
run a campaign, view results and troubleshoot the batch system from the
documentation alone — without asking the author — so that the system is a
product the team owns, not a prototype one person understands.

## What this delivers

A documentation site (`make docs-serve`, built in CI) with four tracks:

- **Getting Started** — prerequisites, deploy, run a single volume, run a
  campaign, view results. Each is a walk-through with the exact commands.
- **How it Works** — architecture with diagrams, the streaming wrapper,
  campaigns and the reconciler's tick, the memory budget, failure handling
  (every exit code and what it costs), the live run log, and a **decision
  log** recording every design choice, the alternatives, and why.
- **Reference** — every chart value, every environment variable, the
  campaign and pipeline YAML formats, the S3 layout and `status.json`, the
  campaign browser.
- **Development** — setup, testing levels, CI, security posture and trust
  boundary, deployment, local k3s, the test log of what was validated on
  hardware.

After the 2026-08-26 audit found the docs describing things that were never
built (X12: `htrq`, `podFailurePolicy`, secret wiring), the affected pages
were rewritten to describe the code as merged (remediation package B2),
and a "trust boundary" section was added.

## Done when

- [ ] The site builds clean and is navigable from the repo.
- [ ] Every chart value and environment variable is documented.
- [ ] A deploy-from-docs on the PoC node succeeds without out-of-band
      knowledge (the local-k3s page was written from doing exactly that).
