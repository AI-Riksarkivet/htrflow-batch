---
type: Product Backlog Item
id: 2884
parent: 2800
title: Supply chain and trust boundary diagram
---

# B50 · Supply chain and trust boundary diagram

**Story.** As a reader of the Security page, I want a diagram showing source → CI build → provenance/SBOM/scan/signature → registry → Kyverno admission → pod, with models taking the same path through ModelPack, and what each control refuses drawn at the point it refuses it, so that the page can be understood from the picture first and the text second.

## Why it matters

This is the picture a NIS2 review and the second audit (B33) will ask for first. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Security* page showing source → CI build → provenance/SBOM/scan/signature → registry → Kyverno admission → pod, with models taking the same path through ModelPack, and what each control refuses drawn at the point it refuses it.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Security* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
