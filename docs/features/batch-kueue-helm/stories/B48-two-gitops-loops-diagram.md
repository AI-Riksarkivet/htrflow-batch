---
type: Product Backlog Item
id: 2882
parent: 2800
title: The two GitOps loops diagram
---

# B48 · The two GitOps loops diagram

**Story.** As a reader of the Campaigns (GitOps) page, I want a diagram showing the platform loop (deployment repo → Argo CD → cluster) beside the work loop (campaigns repo → reconciler → jobs), with what each repo controls and who reviews it, so that the page can be understood from the picture first and the text second.

## Why it matters

The distinction is the single most common confusion for newcomers. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Campaigns (GitOps)* page showing the platform loop (deployment repo → Argo CD → cluster) beside the work loop (campaigns repo → reconciler → jobs), with what each repo controls and who reviews it.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Campaigns (GitOps)* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
