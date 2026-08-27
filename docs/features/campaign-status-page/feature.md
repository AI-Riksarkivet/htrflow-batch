---
type: Feature
id: 2923
parent: 2769
title: HTRflow Campaigns status page
---

# Feature: HTRflow Campaigns status page (#2923)

## In one paragraph

The status page is the batch system's only human-facing screen: every
campaign as a card, every volume with its state, page count, attempts and
links to the live log and the viewer. It is served from the same image as
the viewer, reads two files from the results bucket and nothing else, and
never writes. This feature owns that page: what it shows, how it looks,
how it behaves at archive scale, and how it is tested.

## Settled properties (not stories)

- **Read-only, by design.** Nothing on the page submits, retries or
  changes anything. Changing what runs is a pull request in the campaigns
  repo (B04, B11); changing the platform is a pull request in the
  deployment repo (B12). Write rights are GitOps rights.
- **Internal tool.** The page is reachable only from the office network
  by the people who are meant to see it; there is no login on the page
  itself. Access is a network property (U05 gives it its address).
- **No backend.** The page reads `status.json` and `manifest.json` from
  the bucket; every feature below must keep it that way or say why not.

## Why we are building it

- **"How is it going?" must not require cluster access.** Archivists,
  project leads and operators all ask it; the page is the answer.
- **Honesty over polish.** If the reconciler has stopped, the page says
  so; a stale green tick is worse than no page.
- **It will be the face of the system.** Once campaigns run for the
  archive, this page is what people outside the team see — it needs to
  be in Swedish, accessible by law, and recognisably Riksarkivet's.

## Stories

### Implemented in the repository — awaiting acceptance

| Id | Story |
|---|---|
| [B05](stories/B05-campaign-browser.md) | See progress and live logs in the browser |
| [B22](stories/B22-frontend-tests-and-checks.md) | Frontend tests and checks |
| [B32](stories/B32-audit-fixes-frontend.md) | Audit fixes — the status page degrades gracefully and is safe to link from |

### Not started

| Id | Story |
|---|---|
| [C01](stories/C01-swedish-ui.md) | Swedish user interface |
| [C02](stories/C02-accessibility-dos.md) | Accessibility per DOS-lagen |
| [C03](stories/C03-visual-identity.md) | Riksarkivet visual identity |
| [C04](stories/C04-campaign-detail-page.md) | Campaign detail page |
| [C05](stories/C05-volume-detail-page.md) | Volume detail page |
| [C06](stories/C06-download-results.md) | Download a volume's results |
| [C07](stories/C07-filter-sort-find.md) | Filter, sort and find across campaigns |
| [C08](stories/C08-status-at-archive-scale.md) | Status at archive scale |
| [C09](stories/C09-notify-requester.md) | Notify the requester when a campaign finishes |
| [C10](stories/C10-status-page-dataflow-diagram.md) | Status page data-flow diagram |

B05, B22 and B32 were created under the Batch feature and moved here; they
keep their ids (ids are stable, never renumbered). Related stories
elsewhere: S07 (a search page in the same SPA — Search's deliverable), U05
(the address the page is served at), B20 (the quality score shown per page).
