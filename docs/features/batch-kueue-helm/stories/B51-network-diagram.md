---
type: Product Backlog Item
id: 2885
parent: 2800
title: Network diagram
---

# B51 · Network diagram

**Story.** As a reader of the Security page, I want a diagram showing every pod and the only things it may reach — registry, HCP, IIIF, git, DNS, monitoring — with default-deny drawn as the background, so that the page can be understood from the picture first and the text second.

## Why it matters

Network policies are impossible to review as YAML and easy to review as a picture. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Security* page showing every pod and the only things it may reach — registry, HCP, IIIF, git, DNS, monitoring — with default-deny drawn as the background.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Security* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
