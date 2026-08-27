---
type: Product Backlog Item
id: 2879
parent: 2800
title: Diagram conventions — how every architecture picture is drawn and kept
---

# B38 · Diagram conventions — how every architecture picture is drawn and kept

**Story.** As anyone reading the docs, I want every diagram to use the same shapes, colours for trust zones and level of detail, and to live as text next to the page it explains, so that the pictures read as one system and stay in step with the code through pull requests.

## Why it matters

The productionalisation stories each add a view of the system (B46–B53, U09). Without a shared convention they will look like nine different systems, and a diagram kept as an image file rots silently. Mermaid in Markdown diffs in PRs and renders on the docs site without tooling.

## What this delivers

- A short conventions page: C4 levels used, shape per component type, colour per trust zone (internet / cluster / our registry / HCP), how arrows are labelled, and the rule that a PR changing a component's connections changes its diagram.
- A checked-in Mermaid theme/init block so light and dark mode both render legibly.

## Done when

- [ ] Conventions page exists and every diagram story links to it.
- [ ] Two diagrams by different authors look like the same system.
