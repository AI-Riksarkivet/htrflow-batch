# Campaign browser table redesign — design

2026-08-25, follow-up to the visibility spec. Morgan: the card grid is
underdeveloped; volumes should read as a table; take visual inspiration
from AI-Riksarkivet/rask. Layout, polish level, and row content chosen via
Q&A (table-per-campaign; rask-inspired design pass; failure info + source
manifest link + timestamps).

## Layout (approved mockup)

One section per campaign:

- **Section header**: campaign name, pipeline id chip, volume+pages
  progress (`1/3 volumes · 3/9 pages` + bar), collapse toggle (expanded by
  default, as shipped).
- **Steps line**: the pipeline_steps one-liner (as shipped), styled muted.
- **Volume table** replacing the card grid. Columns:
  `thumbnail (2.5rem) | volume id | status | pages | attempts | updated | links`.
  - status: colored dot + label; `pending` displays as `planned`.
  - pages: `d/t` (pagesLabel without the word "pages").
  - updated: relative-ish short date (`25 Aug 14:32`) from the new
    per-volume `updated` field; `—` when null.
  - links: `open` (viewer, done volumes), `source` (source manifest),
    `log` (failure log, only retry/needs-attention).
- Campaign-level `error` and `orphans` render as full-width notice rows.
- Page header: title left; right side keeps `campaigns repo` link +
  generated-at + stale banner (unchanged logic).

## Visual language (ported from rask, dependency-free)

Port rask's `tokens.css` palette to plain CSS custom properties in the
page's `<style>` (no Tailwind, no imports): warm off-white background
`oklch(0.985 0.004 80)`, foreground `oklch(0.16 0.006 270)`, primary
`oklch(0.37 0.19 250)`, success `oklch(0.65 0.2 145)`, warning
`oklch(0.75 0.18 75)`, destructive `oklch(0.577 0.245 27.325)`, muted
foreground `oklch(0.45 0.012 260)`, border `oklch(0.915 0.006 260)`,
radius `0.625rem`. Dark theme via `@media (prefers-color-scheme: dark)`
using rask's `.dark` values (background `oklch(0.13 0.006 270)`, card
`oklch(0.17 0.008 270)`, primary `oklch(0.68 0.16 250)`, …).

Status→color mapping: done→success, running→primary, queued/retry→warning,
needs-attention/unreachable/unsupported→destructive, planned→muted.

System font stack stays; sizes step down (13–14px table body, tabular-nums
for the pages/attempts columns).

## status.json additions (reconciler)

All nullable/optional, both directions compatible:

- **Per volume `updated: str | null`** — ISO timestamp; for done volumes,
  the manifest.json S3 LastModified. `Bucket.done_volumes` already HEADs
  every manifest: change it to return `dict[str, str]` (volume id →
  ISO-8601 mtime) instead of `set[str]`; the tick keeps using its keys as
  the done-set and passes the mtime into the volume entry. Non-done
  volumes: null.
- **Per volume `failure_log: str | null`** — public URL
  (`<publicResultsBase>/status/failures/<pid>/<vid>.txt`) when status is
  `retry` or `needs-attention`, else null.
- **Upload gap fix**: today the failure log is uploaded only on the
  `retry` path; an exit-13 job goes straight to `needs-attention` with no
  log. Fix: when status is `needs-attention` and the Job still exists,
  upload its logs to the same key (idempotent overwrite, one small
  put_text for an already-failed volume).

## Frontend schema

`volumeEntrySchema` gains `updated: z.string().nullable().default(null)`
and `failure_log: z.string().nullable().default(null)`.

## Testing

- Reconciler: done_volumes mtime dict (Bucket unit test with stubbed
  head_object), `updated` emission (done vs pending), `failure_log`
  emission per status, needs-attention log upload (FakeCluster
  failed_job_logs called, key written).
- Frontend: schema both-directions; a `shortDate` helper test; table
  renders are covered by `bun run check` + build (no component test rig —
  consistent with the repo's current level).

## Out of scope

- Sorting/filtering/pagination (revisit when a campaign exceeds ~100
  volumes).
- The cards view (table replaces it outright).
- Live log tailing; the log link is the uploaded snapshot.
