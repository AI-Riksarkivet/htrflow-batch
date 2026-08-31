---
type: Product Backlog Item
id:
parent: 2923
title: Filter, sort and find across campaigns
---

# C07 · Filter, sort and find across campaigns

**Story.** As anyone using the page once there are many campaigns, I want to filter by state and pipeline, sort by progress or age, and find a campaign or volume by name or reference code, so that the page stays usable at hundreds of campaigns and thousands of volumes.

## Why it matters

The page lists everything in one scroll today, which is right for fifteen volumes and wrong for fifteen thousand. This is the client-side half; C08 is the data half.

## What this delivers

- Filter chips (state, pipeline, needs-attention), sortable columns, a search box over campaign names and volume ids; state kept in the URL so views can be linked; keyboard-operable.

## Done when

- [ ] With a status file of 1 000 volumes the page filters and sorts without visible delay; a filtered view can be bookmarked and shared.
