---
type: Product Backlog Item
id: 2899
parent: 2800
title: Use the quality-prediction step in batch pipelines
---

# B20 · Use the quality-prediction step in batch pipelines

**Story.** As an archivist reviewing a finished campaign, I want each
page's predicted transcription quality to be recorded with the result and
visible in the status page, so that I can direct human checking to the
pages that need it.

## Why it matters

Feature #2770 (*Quality prediction step*, story #2802) adds a step to
`htrflow` that scores each page and writes the score into the ALTO file. The
batch system runs `htrflow` unmodified, so once that step exists, using it
is a matter of naming it in a pipeline file — and then surfacing the score
where people look.

## What this delivers

- A production pipeline file in the campaigns repo that includes the
  quality-prediction step.
- The per-page score picked up into the volume's provenance summary and
  shown in the run-log viewer's per-page grid.

## Done when

- [ ] Depends on #2802 being released in an `htrflow` image.
- [ ] A campaign run with the step produces ALTO files carrying the score.
- [ ] The score is visible per page in the browser.
