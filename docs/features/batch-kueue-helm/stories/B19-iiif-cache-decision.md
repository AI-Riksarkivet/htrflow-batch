---
type: Product Backlog Item
id: 2898
parent: 2800
title: Decide whether an image cache is needed
---

# B19 · Decide whether an image cache is needed

**Story.** As the product owner, I want a clear yes/no on building a
caching layer in front of the image server, based on measurements rather
than opinion, so that we do not build infrastructure we don't need — or
skip infrastructure we do.

## Why it matters

The original design reserved a "Phase 2" cache in case the GPUs spend too
long waiting for images, or in case retries hammer the image server. The
streaming design (B01) was expected to make the first concern largely go
away. The archive-scale run (B16) produces the number that settles it.

## What this delivers

- A decision, not a build: read the B16 figures against the documented
  trigger (roughly >10 % of wall-clock waiting on images, or evidence that
  the image server needs shielding).
- If *yes*: pick the minimal variant (an nginx cache) and split it into its
  own stories. If *no*: record that and close.

## Done when

- [ ] Decision and its numbers recorded in the decision log.
- [ ] Follow-up stories created if the answer is yes.
