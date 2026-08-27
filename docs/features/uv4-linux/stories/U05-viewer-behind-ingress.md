---
type: Product Backlog Item
id: 2905
parent: 2801
title: Give the viewer a real web address
---

# U05 · Give the viewer a real web address

**Story.** As a colleague outside the development team, I want to open the
status page and the viewer at a normal `https://` address on the office
network, so that looking at results does not require ssh or any setup.

## Why it matters

On the proof-of-concept node the viewer and the bucket are reached through
an ssh tunnel to two ports, and the volume manifests are rewritten with
`localhost` addresses to make that work. That is fine for a demo and
useless for a product owner. This story is the viewer's share of the
cluster set-up (B12 on the dev cluster, then B15 on production) and should
be done alongside it.

## What this delivers

- A hostname and TLS certificate for the viewer, and a browser-reachable
  address for the results bucket, configured through the chart's
  `publicResultsBase`.
- The viewer's content-security policy and the bucket's CORS settings
  allowing exactly that address.

## Done when

- [ ] From a laptop on the office network, the status page and a finished
      volume open at the published address with no tunnel.
- [ ] The `localhost` rewriting path is documented as PoC-only and not used.
