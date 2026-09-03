# Frontend: what to improve and add (2026-09-02)

Handoff for an implementing agent. Companion to the wrapper documents in
this directory (`2026-09-02-wrapper-*.md`). Against HEAD `9e074e0` on
`b63-indexed`. Scope: `frontend/` (SvelteKit 2, Svelte 5, static SPA over
the read API `packages/api`). Baseline at audit time: `bun run test` → 71
passed (9 files); `bun run check` → 0 errors, 0 warnings.

State of the code: ~2 300 lines of source, ~1 150 of tests, two routes
(`/` campaign list, `/log` run viewer), a CSP with `script-src 'self'`,
untrusted URLs pass `isHttpUrl` before any href/src, one request in flight
per resource. No audit-grade defect found. What follows is data the UI
already receives and does not show, polling that does more than it needs
to, and the features with the best value per line.

Ground rules: one item per commit, subject ends in `(B63)` or the feature's
story id, no Co-Authored-By trailer, work in this worktree. Every item
keeps `bun run test`, `bun run check` and `bun run lint` green; add a test
for each behaviour change (pure logic in `src/lib/*.test.ts`, component
behaviour in `*.test.ts` beside the component). Read `frontend/README.md`
and `docs/reference/frontend.md` first; update the reference when a route
or query parameter changes. Items 1–4 are the ones to do; 5–7 are
features; 8 is housekeeping.

---

## 1. Stop polling cards that cannot change

**Today.** `CampaignCard.svelte:74` starts a `setInterval(load, RELOAD_MS)`
per card unconditionally. Collapsed cards poll. Cards whose `job.phase` is
`Succeeded` or `Failed` poll. Every poll makes the read API list the Job's
pods live from Kubernetes. Fifty finished campaigns = fifty Kubernetes
queries a minute for nothing.

**Do.** Poll only while `!collapsed` and `job.phase` is `Queued`, `Paused`
or `Running`. In Svelte 5 that is an `$effect` that reads both and returns
the `clearInterval` cleanup, so toggling the card or the phase changing on
the next list poll starts/stops the interval. Still `load(true)` once on
mount regardless, so a finished campaign's table is populated.

**Test.** `CampaignCard.test.ts`: with fake timers, a `Succeeded` job
triggers exactly one detail fetch after `RELOAD_MS × 3`; a `Running` job
triggers four; collapsing a `Running` card stops further fetches.

## 2. Keep the expanded table across polls

**Today.** `CampaignCard.svelte:44–57`: a poll calls `load(true)`, which
fetches `offset=0&limit=200` and replaces the table, so on a campaign with
more than 200 volumes the reader who clicked "load more" is collapsed back
to page one every `RELOAD_MS`. The comment on the function apologises for
it.

**Do.** On a poll, fetch `offset=0` with `limit = Math.min(1000,
Math.max(PAGE, volumes.length))` (the API caps `limit` at 1000; see
`packages/api/README.md`). `loadMore` unchanged. Delete the comment.

**Test.** `CampaignCard.test.ts`: after one "load more" (400 rows), the next
poll requests `limit=400` and the table keeps 400 rows.

## 3. Show the throughput the manifest already carries

**Today.** `run.ts` parses `bytes_fetched` and `pages_per_second` and
`RunSummaryCard.svelte` shows neither; `gpu_stall_seconds` is not in the
schema at all (the manifest has it — `publish.run_manifest` in the wrapper
writes `wall_seconds`, `gpu_stall_seconds`, `pages_per_second`,
`bytes_fetched`).

**Do.** Add `gpu_stall_seconds: z.number().optional()` to
`runManifestSchema`. Add three fields to the summary strip: `pages/s`
(`pages_per_second.toFixed(2)`), `GPU stall` as a percentage of
`wall_seconds` (only when both present and wall > 0), and `fetched` in a
human unit (add a `formatBytes` beside `formatDuration` in `run.ts`). Keep
the fields optional in the markup; older manifests lack them.

**Test.** `run.test.ts` for `formatBytes`; `RunSummaryCard.test.ts` for the
three fields present/absent.

## 4. Deep-link every page into the viewer

**Today.** The completion marker carries `viewer_url` (the `iiif.json` URL;
`publish.run_manifest`), which the UI ignores (it is under
`.passthrough()`). Failed pages, slowest pages and the page grid name pages
but nothing opens them.

**Do.** Add `viewer_url: z.string().optional()` to the schema. A helper
`pageViewerHref(viewerUrl, index)` in `run.ts` returning
`uv.html#?manifest=<viewerUrl>&cv=<index-1>` when `isHttpUrl(viewerUrl)`,
else `null`. Pages are `"0001"`-style names, index = `Number(id) - 1`
(canvas order is manifest order; `iiif.json` omits pages without ALTO
dims, so the index can drift for a volume with unparsable pages — accept,
document in the helper). Use it on the failed-page ids, the slowest-page
chips and as the click action of a `PageGrid` cell (open in a new tab,
`rel="noopener"`).

**Verify first.** That the RA UV4 fork honours the `cv` hash parameter
(upstream UV does: `#?manifest=…&cv=<canvasIndex>`). Open
`/uv.html#?manifest=<any finished iiif.json>&cv=3` on the devstack.

**Test.** `run.test.ts` for the helper (http check, index math, null on a
non-http URL). Component test: a failed page renders the link.

---

## 5. Filter and find (feature)

- Campaign list (`routes/+page.svelte`): one text input filtering the
  loaded `jobs` by `name`, `namespace`, `pipeline` and `phase`
  (case-insensitive substring), as a `$derived` over the list; keep it in
  the URL (`?q=`) so a filtered view can be shared. No new API call.
- Volume table (`CampaignCard.svelte`): a "failed only" toggle over the
  loaded rows. Note it filters the loaded page only; say so in the label
  when `hasMore`.

Tests: pure filter function in a new `lib/filter.ts` + test; one component
test each.

## 6. Tail-first log rendering (feature)

**Today.** `/log` parses and renders the whole log on every live tick
(`parsed = $derived(parseRunLog(logText))`, all groups rendered). The run
log is capped at 4 MiB by the wrapper; a long live run re-renders thousands
of nodes every 15 s.

**Do.** Keep `parseRunLog` whole (it is cheap; `isTerminalLog` already
looks at the tail) but render only the last N lines (N = 500) by default
with a "show all (M lines)" button; when the reader clicks it, render
everything and keep it that way for the page's life. Sticky-bottom logic
unchanged. Trim from the group list, not the text, so groups stay intact.

**Test.** `routes/log/page.test.ts`: a 2 000-line log renders ≤ 500 lines
plus the button; clicking renders all.

## 7. Progress bars (feature; second half depends on the wrapper)

- Per campaign, now: a bar from `job.counts` (done / total, failed in red)
  under the card header. Pure CSS, values already there.
- Per volume, later: needs the progress heartbeat
  (`status/progress/<pipeline>/<volume>.json`, item 3 of
  `2026-09-02-wrapper-feature-ideas.md`) surfaced by the read API as
  `progressUrl` on `VolumeView`; then the row shows done/pages and an ETA
  while `state === "active"`. Do not build the frontend half first.

## 8. Housekeeping (optional, small)

- `altoPrefix` is parsed (`api.ts:84`) and never used. Either show a
  "results" link to it or drop it from the schema and the API doc.
- `getJson` in `api.ts` never passes the `AbortController` signal to
  `fetch`; the list page "aborts" by ignoring the result, which is what the
  comment describes, so this is cosmetic. If touched, pass the signal and
  treat `AbortError` as silent.
- `isTerminalLog`'s regex matches the wrapper's failure line
  `(permanent|transient) failure in <stage>:`; the wrapper now appends
  ` errors: …` detail to verify failures — still matches. No change, noted
  so nobody "fixes" it.

---

## Not now

- Virtualised log or table rendering: item 6 is enough at the current cap.
- Authentication or write actions (pause/resume from the UI): the read API
  is read-only by construction and tested to stay that way
  (`packages/api/README.md`); a write path is a design decision, not a
  frontend feature.
- A campaign rollup page: wants the API endpoint from the wrapper ideas
  list (item 5 there) first.

## Verification after every commit

```bash
cd /home/morgan/htrflow-batch/.worktrees/b63-indexed/frontend
bun run test
bun run check
bun run lint
bun run build     # the static site still builds; CSP hash unchanged
```

Suggested order: 1 → 2 → 3 → 4 → 5 → 6 → 7.
