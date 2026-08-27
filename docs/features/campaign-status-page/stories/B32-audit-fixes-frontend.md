---
type: Product Backlog Item
id: 2861
parent: 2923
title: Audit fixes — the status page degrades gracefully and is safe to link from
---

# B32 · Audit fixes — the status page degrades gracefully and is safe to link from

**Story.** As anyone reading the status page, I want one bad field in the
data to show a warning rather than a blank screen, the page to load fast on
a phone, and every link on it to be one the system generated — so that the
only human-facing surface of the batch system is one people can trust.

## What was found (audit package A4)

- **X9 (high)** — one malformed field in `status.json` blanked the whole
  page; a transient poll error hid an already-rendered page.
- **X10 (high)** — thumbnails fetched 6.7 MB of source images to paint
  eight 26-pixel icons.
- **X13 (medium)** — URLs from campaign data and the query string reached
  `href` unvalidated.
- **X18 (medium)** — accessibility (contrast, keyboard) and small-screen
  layout.

## What was done

- Fail-soft schemas: a bad field renders as `unknown` with a banner; poll
  errors are counted and capped, never blanking a rendered page.
- The wrapper now writes a first-page `thumb.jpg`; the reconciler
  advertises it sized-or-null; the page never fetches source images.
- URL allow-listing for every link (scheme and host); a content-security
  policy served by the viewer; `/config.js` from the chart so the status
  URL follows `publicResultsBase`.
- AA-contrast tokens, shared theme, keyboard navigation, small-screen
  tables and log lines; one clock for the live poll; run viewer proven on
  a 480-page volume.
- Component and route tests for all of the above (B22).

## Done when

- [ ] A `status.json` with a corrupted field renders every other campaign
      and shows a warning (tested).
- [ ] Page weight on the campaign list is independent of volume count.
- [ ] A hostile URL in campaign data is not rendered as a link (tested).
