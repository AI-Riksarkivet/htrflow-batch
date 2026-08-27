---
type: Product Backlog Item
id: 2904
parent: 2801
title: Get our viewer changes into the Riksarkivet fork
---

# U04 · Get our viewer changes into the Riksarkivet fork

**Story.** As the team maintaining the viewer, we want the fixes from U02
merged into the `Riksarkivet/universalviewer4` repository itself, so that
the Linux build can follow the fork's updates instead of being frozen at
one commit.

## Why it matters

The build pins one upstream commit because our fixes are a patch file that
applies cleanly only there. Every improvement made to the fork since is
invisible to us until someone re-derives the patch by hand. Merging the
fixes upstream removes the patch and the pin.

## What this delivers

- A pull request to the fork with the three changes (config fetch, overlay
  scaling, persistent outlines), reviewed and merged.
- The Linux build switched to the fork's main branch; the patch file
  deleted.

## Done when

- [ ] PR merged in `Riksarkivet/universalviewer4`.
- [ ] `BuildViewer` builds from `main` with no patch and U02's acceptance
      criteria still hold.
