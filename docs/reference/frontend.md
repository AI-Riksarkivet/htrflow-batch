# Campaign Browser

The SvelteKit SPA served at `/` by the web image — three routes, no server,
reading the read API (`packages/web`) that serves it. Source:
[`frontend/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/frontend);
the `frontend/README.md` there is the developer-facing version of this page.

- `/` — every campaign, one card per Indexed Job, each card the same four
  zones in the same order so that ten of them scan like ten rows of one
  table (below), then the folded card's one-line strip or its **volume
  table** (id, state chip, how far that volume has got, links). Each card
  fetches its own volumes, paged. The list is ordered by what wants a person
  — running, then anything wrong, then finished newest-first, then not
  started (`src/lib/order.ts`) — and a failed poll puts a banner in plain
  words over the last list it received. The page header carries the logo
  (`static/ra.svg`) and title on the left, and on the right the deployed
  release (`GET /api/v1/version`), a link to the
  [source repository](https://github.com/AI-Riksarkivet/htrflow-batch) as the
  GitHub mark (inline SVG, labelled, keyboard reachable) and the theme
  toggle.
- `/log?log=<url>&manifest=<url>[&live=1]` — the **run viewer**: the
  wrapper's run log grouped by stage, plus a summary card from
  `manifest.json` (ok/failed/skipped counts, total + wall, median/p95/max,
  the five slowest pages, failed pages with their errors, one cell per page
  coloured by status and scaled by seconds, and the full table behind
  `<details>` in slices of 100 — readable at 480 pages). With `live=1` it
  re-fetches on the wrapper's log-ship cadence and stops on the terminal
  line, a manifest that covers every page, or after 20 failed polls. Each
  row of the full table also carries an **alto** column (below) once the
  manifest has a `viewer_url`.
- `/alto?src=<url>` — the **ALTO viewer**: one page's ALTO XML as text, in
  reading order, each line tinted by its `WC` confidence, with a raw-XML
  toggle. Reached from the run viewer's alto column; see
  [Viewing Results](../getting-started/viewing.md).

## Stack

Svelte (runes) + SvelteKit with `adapter-static` (`prerender = true`,
`ssr = false` — a pure static shell, data fetched in the browser), strict
TypeScript, Zod at the boundary, Vitest (+ @testing-library/svelte on
jsdom), Prettier, Bun as the package runner. The supported Node and Bun
versions are the `engines` field of `frontend/package.json`.

| File                                                 | Description                                                                                                                                                 |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/config.ts`                                  | API base and cadence resolution (table below)                                                                                                               |
| `src/lib/api.ts`                                     | Read-API Zod schemas (`JobSummary`, `JobDetail`, `VolumeView`, `VolumeProgress`), `fetchJobs`/`fetchJob`, `ApiUnreachable`, and the pure view helpers `isHttpUrl`/`shortDate` |
| `src/lib/run.ts`, `runlog.ts`                        | `manifest.json` schema + summary math (incl. `scale()`, the page grid's bar height; each page's `alto` URL); run-log grouping and the terminal-line check   |
| `src/lib/alto.ts`                                    | `parseAlto` (ALTO XML → text lines + confidence), `altoUrl`, `prettyXml` (the raw-XML toggle)                                                              |
| `src/lib/pipeline.ts`                                | `pipelineModels` (the models a pipeline YAML names, and what pins them), `modelLabel`, `modelUrl`                                                            |
| `src/lib/reasons.ts`                                 | `describeReason`, `describeApiError` and the other sentence builders — the one place a message a person reads is written                                    |
| `src/lib/poll.ts`                                    | the one poller behind every route: one request in flight, nothing fetched while the tab is hidden, the wait doubling on consecutive failures                |
| `src/lib/theme.svelte.ts`                            | the one theme store (`ThemeToggle.svelte` on every route)                                                                                                   |
| `src/lib/components/`                                | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle`                                                                                   |
| `src/routes/+page.svelte`, `routes/log/`, `routes/alto/` | the three routes                                                                                                                                        |
| `src/app.css`                                        | design tokens per theme (AA-checked), reduced-motion, and the header/chip/code-block chrome shared by every route (kept out of each route's scoped styles) |
| `static/config.js`                                   | `window.API_BASE`/`RESULTS_BASE` for a `bun run dev`; in a deployment the read API serves its own `/config.js`                                              |

## Configuration

| Setting                | Runtime (deploy)                             | Build time           | Default                                    |
| ---------------------- | -------------------------------------------- | -------------------- | ------------------------------------------ |
| read API base          | `window.API_BASE`, served in `/config.js`     | `VITE_API_BASE`      | `/api/v1`                                  |
| results base           | `window.RESULTS_BASE`, served in `/config.js` | `VITE_RESULTS_BASE`  | *(empty — see below)*                      |
| campaign list re-fetch | —                                            | `VITE_RELOAD_MS`     | `60000`                                    |
| live-log re-fetch      | —                                            | `VITE_LIVE_MS`       | `15000` (the wrapper's `LOG_SHIP_SECONDS`) |
| live-log give-up       | —                                            | —                    | `LIVE_MAX_FAILURES = 20` attempts          |
| poll backoff ceiling   | —                                            | —                    | `MAX_POLL_MS = 300000`                     |

`/config.js` is **served by the read API**, not read out of a file: the same
process that answers `/api/v1` writes it from its own environment
(`packages/web`, `CONFIG_JS`), so `window.RESULTS_BASE` is always the
`HTRFLOW_PUBLIC_RESULTS_BASE` the API builds its result URLs from and cannot
drift from it. There is nothing for a deployment to overwrite: set
`publicResultsBase` on the chart and the page follows. `static/config.js` is
the same file for a `bun run dev`, which has no service to ask; it ships an
empty `RESULTS_BASE`, which the run-log route reads as "nobody said" and
falls back to accepting any absolute http(s) URL.

The API base is resolved on every fetch, highest first: `window.API_BASE`,
then `VITE_API_BASE`, then the default. **The page ships a CSP**
(`svelte.config.js`, `kit.csp` in `hash` mode: `script-src 'self'` plus the
hash of SvelteKit's own init script, `object-src 'none'`, `base-uri 'self'`),
which is why the configuration arrives as a same-origin file loaded before
the app and never as an inline `<script>` in `index.html` — that the CSP
blocks. `/api/v1` is same-origin because the read API is the process serving
the page, so `script-src 'self'` already covers `/config.js` (no
`connect-src` directive is set, so fetches are unrestricted by this CSP; the
only restriction is on what may _execute_ as script). A CSP header from the
server must not be stricter than the meta tag (the browser enforces the
intersection); `packages/web` adds `frame-ancestors 'none'` to every
response, and a policy of its own to `/uv.html`, which has no meta tag —
Universal Viewer is not built by this project.

## Derivation rules

- **Fail-hard shape, fail-soft rows.** The read API is ours, not an
  untrusted document: `src/lib/api.ts` parses every response with Zod, and
  a response of the wrong shape throws. One campaign row the page cannot
  read is still a bug, but not a reason to hide the rest: `fetchJobs`
  leaves that row out, counts it, logs the first issue to the console for
  the operator, and the page says how many campaigns are hidden in a
  banner over the list it could read; a list whose every row is unreadable
  throws like a wrong shape. `ApiUnreachable` covers a network error and a
  non-2xx status alike; the page shows one banner over the last good list.
  There is no age-based staleness check — every response is computed live
  from the Kubernetes API, so there is nothing that can go stale the way a
  stored status document would.
- **States.** A `VolumeView.state` is `pending`, `active`, `done`, or
  `failed` — computed by the API from the Job's index sets, not stored
  anywhere; a `failed` row's `reason` is `{stage, permanent, error}` parsed
  by the API out of the wrapper's termination message (`stage`/`permanent`
  `null` when it was not the wrapper's JSON), present only while a pod for
  that index still exists. A `done` row whose `progress.failed` is above
  zero takes the **warning colour** rather than the green one — the volume
  published, but not all of its pages — and carries "done with N failed
  pages" as its title and as what a screen reader reads, so the colour is
  never the only thing carrying it; the folded card's one-line strip follows
  the same rule.
- **The "job removed" chip.** A campaign whose Job is past its
  `ttlSecondsAfterFinished` arrives with `jobGone: true` — the API served
  that row from the campaign's ConfigMap and the status ConfigMap beside it,
  which have no TTL
  ([The record a campaign leaves](../how-it-works/campaigns.md#the-record-a-campaign-leaves)).
  Its `phase` is `Unknown` ("outcome unknown", styled like queued/paused, not
  like a failure) whenever no terminal record was ever written for it — a Job
  deleted by hand or by a prune — since a `Running` that can never change is
  the one answer that is certainly wrong.
  The card wears a neutral chip saying so, beside (never instead of) the
  phase chip: the Job's removal is housekeeping, not a verdict on the
  campaign, and the campaign's own phase is still the verdict. The meta line
  adds `finished <date>` whenever the API sends `finishedAt`, which past the
  TTL is the only date that still means anything. Such a card has no volume
  table — the per-index states were the Job's — only the failed volumes the
  record kept, with their one sentence each.
- **Progress.** A row's `progress` (`{done, total, failed, lastPage, stage,
  updatedAt, ageSeconds, lastError, errors, viewerPublished}`, or `null`) is
  what the API read out of that volume's `progress.json` in the bucket — the
  one thing the Kubernetes API cannot answer. `describeProgress` renders it
  under the state chip as "137 / 638 pages · processing pages · updated
  12 s ago": the stage word comes from the same map a failure sentence uses,
  a stage this build does not know is shown as it came rather than dropped,
  and "updated Ns ago" comes straight from `ageSeconds` — computed by the API
  from its own clock at fetch time, never from comparing `updatedAt` against
  the browser's, so a reader's clock skew cannot show "0 s ago" for a row
  that has not actually just updated. `null` renders nothing at all. The
  campaign header adds the API's summed `pagesDone`/`pagesTotal`.
- **Running motion.** Only what is running moves, so that a campaign still
  working cannot be mistaken for a finished one between polls: the state and
  phase chips carry a pulsing dot (`aria-hidden` — the chip's word is the
  state; the header's needs a succeeded warm-up too, since `Running` is also
  what a campaign reads as while its warm-up is pending), an active row and a
  Running header whose summed `pagesTotal` is above zero carry a 3px
  `role="progressbar"` bar whose fill eases to `done`/`total` over 600 ms;
  the sheen crosses a bar only while the work behind it is actually
  happening, and the progress line whose `done` actually changed since the
  last poll fades a second of the running blue out from behind its text.
  Under `prefers-reduced-motion: reduce` there is no pulse, no sheen and no
  fade — the bar still shows the same fraction, it just jumps to it.
### The four zones of a campaign card

Every card has the same four zones, always in this order and always the same
shape, because the page's real job is a list: a reader scanning ten campaigns
should find each fact in the same place on each one, the way they would in a
table.

1. **Identity and state**, one line. The left accent bar, the campaign's
   `namespace/name` as the fold toggle, then the pipeline chip, the phase
   chip (with its pulsing dot while the campaign runs, and the warning colour
   when it finished with pages missing), the warm-up chip while the warm-up
   has not succeeded, and the "job removed" chip. At the right end of the
   same line, the two ends of the run as a range — `14 Sept, 10:56 → 11:00`.
   The arrow is the whole device: "created … finished …" needed two words to
   say what it says on its own. A run that finished the same day gives its
   end the clock only; one still going ends in an ellipsis, with "not
   finished" for assistive tech. Both halves stay `<time>` elements carrying
   the exact timestamp in `datetime` and `title`.
2. **Numbers**, one line of fixed columns — `volumes`, `pages` and, only when
   there are any, `errors`. Each cell is a label, a 3px bar and its figures
   (`2 / 4 · 2 failed`), and the column tracks are fixed lengths rather than
   content-derived, so the figures of stacked cards sit on one vertical line.
   A total nobody knows yet (a campaign that has not run: the API reads page
   counts out of the bucket) shows the label and an em dash, never a bar of
   nothing over nothing. `errors` counts ERROR-and-worse only — the wrapper's
   own benign WARNINGs, a pipeline rebuild, "manifest covers n/m pages", must
   not read as something wrong on a healthy run.
3. **Problems**, one line, and only when there is one: why the warm-up could
   not run, then each failed volume as `id: sentence`, then the most recent
   page error, then a link to the run log of the volume that error came from
   (the API sends that volume's `logUrl`, since the row it happened in is
   usually outside the page being shown). It carries sentences and nothing
   else — the counts are zone 2's job and are not repeated here. Warning
   colour, clipped to one line, with the whole of it in the `title` and in a
   visually-hidden copy beside it, because a tooltip alone is not
   keyboard-reachable. With the card open it drops the failed volumes that
   are already visible as rows in the loaded table and keeps the rest.
   `describeLastError` names the failing page once: the wrapper writes it
   into its own message as often as not, and the API sends it beside the
   message, which read "page 0044: page 0044: …" until this was fixed.
4. **Models**, one quiet line: which weights produced these results. It is
   the least often read line on the card, so it sits last and lightest.
- **Sentences, not fields.** No reader ever sees `reason`'s fields, a
  `ZodError` or a transport string: `src/lib/reasons.ts` turns a `reason`
  into one sentence (`describeReason`) and a failed fetch into one sentence
  (`describeApiError`), each saying what happened, where, and what to do
  next. Which sentence a `reason` gets is decided by its **fields**, never by
  matching on the error text: a bad env and an unreadable manifest send their
  reader to different files, and the wrapper's `stage` (`config` vs `setup`)
  is what tells them apart. It is the single place where a message a person
  reads is written — the wording is pinned verbatim in `reasons.test.ts`, and
  the table is in [Failure handling](../how-it-works/failure-handling.md). A
  `reason` the API could not parse renders as "the pod stopped without a
  message this page can read", never as the raw JSON.
- **Phase.** A campaign's `JobSummary.phase` (`Queued`/`Paused`/`Running`/
  `Succeeded`/`PartiallyFailed`/`Failed`) drives the card's left accent: red
  if `Failed`, `PartiallyFailed` or any volume is `failed`, blue if
  `Running`, green if `Succeeded`, grey otherwise. `PartiallyFailed` — the
  Job gave up with some indexes already published — shows as "partially
  failed" in the warning colour, not the error colour: part of the campaign
  did come out. A `Succeeded` campaign whose `pagesFailed` is above zero
  takes that same warning colour, on the phase chip and on the accent, with
  the same "done with N failed pages" title: every index published and pages
  were still lost inside them, and zone 2's bars go amber with it.
- **Log link** —
  `log?log=<encodeURIComponent(logUrl)>&manifest=<encodeURIComponent(manifestUrl)>`,
  plus `&live=1` for a volume whose `state` is not `"done"`. Both URLs come
  off the same `VolumeView` row: `manifestUrl` is what feeds `/log`'s
  `RunSummaryCard`, and `logUrl` is absolute and bucket-rooted
  (`<public_results_base>/status/logs/<pipeline>/<id>.txt`, no
  namespace/`S3_PREFIX` prefix): the browser has no bucket base URL to
  resolve a bare key against, so the API builds the full URL — see
  [Events and signals](../how-it-works/signals.md).
- **ALTO column.** `RunManifest.viewer_url`
  (publish.py: `<public_results_base>/<S3_PREFIX><pipeline>/<volume>/iiif.json`,
  i.e. `Config.volume_prefix`) is typed in `runManifestSchema`, not just
  passed through; `pageStats`/`summarizeRun` derive each page's ALTO URL
  alongside it — `altoUrl(viewer_url, pageId)` swaps `iiif.json` for
  `alto/<pageId>.xml`, the sibling directory `viewer.py`'s `seeAlso` already
  points at — and attach it as `PageStat.alto` when `viewer_url` is a valid
  http(s) URL (a manifest without one has no alto column).
  `PagesTable`'s **alto** cell renders `view` (`/alto?src=<encodeURIComponent(url)>`)
  and `download` (fetch + `Blob` + a same-origin object URL — `<a download>`
  is ignored cross-origin, and the results bucket is a different origin from
  this page) when a row has one, nothing otherwise. `/alto` itself parses the
  fetched XML with `lib/alto.ts`'s `parseAlto` (namespace-agnostic —
  `getElementsByTagNameNS("*", …)` — so a default namespace, a prefix, or
  none all work) into text lines tinted by `WC` in four buckets
  (high/medium/low/unknown, a legend line names the cutoffs), with a raw-XML
  toggle (`prettyXml`) and three plain-sentence errors: unreachable, not
  valid XML, and valid XML with no text lines.
- **Open, before it is finished.** The **open** slot points at the volume's
  `iiifUrl` as soon as `state` is `done` **or** `progress.viewerPublished` is
  true: the wrapper republishes `iiif.json` every ten pages, so a running
  volume is readable in the viewer long before it publishes — but only once
  that PUT has actually succeeded, never merely because a page count crossed
  zero (a volume smaller than the ten-page cadence, or one just past a page
  but before its own interim publish, would otherwise link to a manifest that
  is not there yet). Only a volume with nothing published yet falls back to
  its source manifest.
- **Three link slots.** Every volume row — and the folded card's latest
  strip — renders the same three fixed slots, **open · source · log**, from
  one snippet, so a missing link leaves a gap instead of shifting its
  neighbours and the eye can scan a column of "source" straight down.
  - **open** — `uv.html#?manifest=<url>`: the published `iiifUrl` once the
    volume is `done` or `progress.viewerPublished`, the volume's own
    `sourceUrl` before that, so the viewer is reachable from the first tick.
    Empty when there is neither.
  - **source** — `VolumeView.sourceUrl`, the URL half of the volume's
    `volumes.txt` line, straight to the source manifest. Empty for an
    `images:` volume, which lists bare image URLs and has no manifest, and
    empty for anything that is not an absolute http(s) URL: `volumes.txt` is
    a file humans edit in a git repo, so `isHttpUrl` guards it again at the
    last step before it becomes an href (this also gates the "open"
    fallback).
- **Pipeline chip.** A button once the detail has loaded: its `title` is
  `JobDetail.pipelineSteps` joined by ` → `, and clicking it toggles
  `JobDetail.pipelineYaml` in an inline `<pre>` (`aria-expanded` /
  `aria-controls`). Both fields come from the `htr-pipeline-<id>` ConfigMap;
  when it is gone the chip stays a static label with nothing to toggle.
- **Models line.** The left half of the card's quiet meta line, the small
  muted row at the foot of the header block it shares with the created and
  finished dates: one link per model the pipeline loads, in step order,
  separated by `·` and clipped with a title of the whole list when the card
  is too narrow for it — `<repo name> @<short revision>`, or
  `<repo name> unpinned` when nothing pins it, linking to
  `https://huggingface.co/<id>/tree/<revision>` (`/tree/main` unpinned) and
  guarded by `isHttpUrl` like every other href here. `src/lib/pipeline.ts`
  reads `JobDetail.pipelineYaml` line by line rather than with a YAML
  library: the document is one we render, and only two keys are wanted from
  it — `model_settings.model`, and the revision from either
  `model_settings.revision` (YOLO) or `model_settings.model_kwargs.revision`
  (TrOCR, Donut, DiT), the same two placements the cluster's Kyverno
  model-revision policy accepts. A step with no model (`Export`) contributes
  nothing, and a pipeline with no models renders no line. These are the
  models htrflow's own `Processing` block names in every ALTO the campaign
  publishes ([From image to transcription](../how-it-works/page-flow.md)).
- **Running build.** `GET /api/v1/version` answers
  `{version, web}`: `version` is the tag both images are published under,
  baked in at build time as `HTRFLOW_BATCH_VERSION` (`dev` for an unstamped
  build) and read by the service from its own environment like every other
  setting (`kube.Config`); `web` is the `packages/web` package version, which
  moves independently of the release. The header shows
  `htrflow-batch <version>` — what the operator deployed — with `web` in the
  tooltip. Read once per page load, since nothing can change it while the
  page is open; a version the page could not fetch is simply absent from the
  header, never an alert over the list.
- **Warm-up chip.** Beside the pipeline chip whenever `JobSummary.warmup`
  isn't `succeeded`: "warm-up pending/running/failed" or "no warm-up"
  (`missing`). `failed` also pushes the card's left accent to the failed
  colour — the campaign's pods cannot start without it. `missing` pushes the
  accent only while the campaign's own phase is **not** `Succeeded`: an old
  pipeline that never had a warm-up Job must not paint a finished campaign
  red. The chip itself shows either way. A `failed` match's `title` (and,
  with the card open, a line under the chip) is
  `describeReason(warmup.reason)`; there is no warm-up log to link
  instead.
- **Folded by default.** A card starts collapsed and remembers the reader's
  choice in `localStorage` under `htrflow.card.<namespace>/<name>` — every
  access wrapped, since a browser may refuse storage; the card then simply
  forgets. While folded it still shows the failures block and a one-line
  **latest strip**: `JobDetail.latest` — the newest `active` volume, else the
  newest `done` one, else nothing — with the same three link slots, so UV and
  the run log stay one click away. The API computes it over **every** volume,
  like `failures` and unlike `volumes`: picking it in the browser would only
  ever see the page that happens to be loaded, and for a campaign of
  thousands the index in flight is never in the first 200.
- **No thumbnails.** The read API has no per-volume image field; the volume
  table is id / state / links only.
- **Failures block.** `JobDetail.failures` (up to 50 newest
  failed-with-a-reason rows, computed over every volume, independent of the
  volume table's paging) is rendered as a compact callout above the volume
  table. It shows only the failures the reader cannot already see: folded,
  that is all of them, under the heading `failures (<n>)`; with the table
  open, the callout drops every failure whose row is on a loaded page and
  the heading becomes `failures not shown below (<n>)`, so a failure is
  never listed twice. It is not rendered at all when that filtered list is
  empty — an open table holding every failure shows no callout. One line per
  entry, `<id> — <sentence>` from `describeReason` (CSS-clamped to one line,
  no JS truncation), each line linking to the same `logHref` as its table
  row.
- **Paged volumes.** `CampaignCard` fetches its own volumes via `fetchJob`
  (`offset`/`limit`, default page 200), independently of the campaign list
  poll on `/`; a "load more" button pages in the next batch when
  `counts.total` exceeds what has loaded. A poll re-fetches **every page
  currently open** (`offset 0`, the limit rounded up to whole pages and
  capped at the API's own `limit` ceiling of 1000, rows past that left as
  last fetched), so a tick never undoes "load more".
- **Accessibility** — campaign header is a disclosure button, carrying
  `aria-controls` only while the volume table is rendered (a folded card has
  no table, and a dangling IDREF is invalid ARIA — `aria-expanded` carries
  the state on its own); AA contrast in both themes;
  `prefers-reduced-motion` honoured; no horizontal overflow at 390 px.

## Commands

```bash
cd frontend
bun install
bun run dev        # Vite dev server; static/ is served at /
bun run test       # vitest (pure + component tests, jsdom)
bun run coverage   # vitest with @vitest/coverage-v8
bun run check      # svelte-check, strict TypeScript
bun run lint       # prettier --check
bun run format     # prettier --write
bun run build      # static build → dist/, consumed by .docker/htrflow-web.dockerfile
```

The web image (`.docker/htrflow-web.dockerfile`, port 8081) builds this SPA
in its first stage and copies it into `/app/static` over the Universal
Viewer build, so `/` is the SPA, `/uv.html` is UV and `/api/v1/…` is the
read API — one origin, no proxy. `make build-web` builds the whole thing;
nothing has to be staged by hand.
