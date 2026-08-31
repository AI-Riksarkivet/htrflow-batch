---
type: Product Backlog Item
id:
parent: 2923
title: Notify the requester when a campaign finishes
---

# C09 · Notify the requester when a campaign finishes

**Story.** As the data scientist who submitted a campaign, I want a message when it completes or when it needs attention — email or Teams, to the address named in the campaign file — so that I do not have to keep the page open for a week.

## Why it matters

Ops alerts (B60) go to on-call; the requester is a different audience with a different question ("is my material done?"). The campaign file is the natural place to say who to tell.

## What this delivers

- An optional `notify:` field in the campaign YAML (validated by the reconciler); a small notifier that watches the status index (C08) for state transitions and sends via the agency's mail relay or a Teams webhook, with links to the campaign page.
- Notifications are per transition, not per tick; a test campaign produces exactly one "done" message.

## Done when

- [ ] A campaign with `notify:` on DEV sends one completion message and one needs-attention message with working links; a campaign without it sends nothing.
