---
type: Product Backlog Item
id: 2887
parent: 2800
title: Campaign lifecycle state diagram
---

# B53 · Campaign lifecycle state diagram

**Story.** As a reader of the Failure Handling page, I want a diagram showing a volume from YAML commit → pending → queued → running → done / failed / needs-attention, with the retry, resume and orphan transitions and which component drives each, so that the page can be understood from the picture first and the text second.

## Why it matters

The sequence diagram shows the happy path; on-call needs the states and how a volume leaves each one. Drawn as Mermaid per the conventions (B38) and placed on the page it explains.

## What this delivers

- One Mermaid diagram on the *Failure Handling* page showing a volume from YAML commit → pending → queued → running → done / failed / needs-attention, with the retry, resume and orphan transitions and which component drives each.
- A three-line legend under it; the diagram referenced from the feature page where relevant.

## Done when

- [ ] Renders on the docs site in light and dark mode on the *Failure Handling* page.
- [ ] Follows the conventions page; reviewed by someone who did not draw it.
