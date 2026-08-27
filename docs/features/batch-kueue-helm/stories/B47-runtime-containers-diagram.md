---
type: Product Backlog Item
id: 2881
parent: 2800
title: Runtime containers diagram refreshed (C4 level 2)
---

# B47 · Runtime containers diagram refreshed (C4 level 2)

**Story.** As a reader of the Architecture page, I want a diagram showing reconciler, Kueue, GPU jobs, warm-up, exporter, viewer, browser, and what each reads and writes — the existing diagram brought up to date with the exporter (B40), the registry (B36) and models from the registry (B35), so that the page can be understood from the picture first and the text second.

## Why it matters

The existing picture predates half the components. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Architecture* page showing reconciler, Kueue, GPU jobs, warm-up, exporter, viewer, browser, and what each reads and writes — the existing diagram brought up to date with the exporter (B40), the registry (B36) and models from the registry (B35).
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Architecture* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
