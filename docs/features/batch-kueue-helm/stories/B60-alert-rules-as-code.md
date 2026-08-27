---
type: Product Backlog Item
id: 2878
parent: 2800
title: Alert rules as code
---

# B60 · Alert rules as code

**Story.** As the operator on call, I want the conditions that mean "someone must look" — stale status, reconciler failing, needs-attention volumes rising, GPUs idle with a non-empty queue, HCP write errors — defined as alert rules in the chart and routed to the on-call channel, so that a stuck system pages a person instead of showing a stale web page.

## Why it matters

The status page already shows STALE and error banners, but a banner only helps someone who is looking. Alerts as code go through the same review and promotion as everything else.

## What this delivers

- `PrometheusRule` in the chart with: status stale (> 3 ticks) · reconciler tick failing · needs-attention count increasing · GPU idle > N minutes with a non-empty queue · HCP write errors; each with a runbook link (B57).
- Routing to the on-call channel configured per environment through values.

## Done when

- [ ] Each rule has been triggered once on DEV and reached the on-call channel; the runbook link resolves.
- [ ] Installed by the chart in every environment.
