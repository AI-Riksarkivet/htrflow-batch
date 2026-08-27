---
type: Product Backlog Item
id: 2883
parent: 2800
title: Environments and promotion diagram
---

# B49 · Environments and promotion diagram

**Story.** As a reader of the Deployment & Promotion (B54) page, I want a diagram showing DEV → staging → prod, one Argo CD application per environment, the deployment repo folders, Kargo Warehouse/Stages/Freight and where the human approval sits, so that the page can be understood from the picture first and the text second.

## Why it matters

Operators need to see the path a release takes before they trust a promotion. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Deployment & Promotion (B54)* page showing DEV → staging → prod, one Argo CD application per environment, the deployment repo folders, Kargo Warehouse/Stages/Freight and where the human approval sits.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Deployment & Promotion (B54)* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
