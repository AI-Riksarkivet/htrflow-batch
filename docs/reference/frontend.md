# Campaign Browser

The SvelteKit SPA served at `/` by the web image — three routes, no server,
reading the read API (`packages/web`) that serves it. Source:
[`frontend/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/frontend);
the `frontend/README.md` there is the developer-facing version of this page.

- `/` — every campaign, one card per Indexed Job, each card the same four
  zones in the same order so that ten of them scan like ten rows of one
  table (below) and ending in a quiet provenance footer, with the folded
  card's single volume row or the whole loaded page of them between them —
  all of it one grid (below). Each card
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
  toggle. Reached from the run viewer's alto column; like `/log`, it reads
  only a URL under the results base (`isResultUrl`). See
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
response, and a policy of its own to the viewer's `uv.html`, which has no
meta tag — Universal Viewer is not built by this project. That policy is
chosen by the file served, not by the path asked for, so `/uv`, `/uv.html/`
and every other path the static mount resolves to the same file carry it
too.

## Derivation rules

- **Fail-hard shape, fail-soft rows.** The read API is ours, not an
  untrusted document: `src/lib/api.ts` parses every response with Zod, and
  a response of the wrong shape throws. One campaign row the page cannot
  read is still a bug, but not a reason to hide the rest: `fetchJobs`
  leaves that row out, counts it, logs the first issue to the console for
  the operator, and the page says how many campaigns are hidden in a
  banner over the list it could read; a list whose every row is unreadable
  throws like a wrong shape. One field is read on its own:
  `VolumeView.sourceUrl`, a line of a file people edit rather than a URL the
  API built, is `null` when it is not a URL this browser can use, so one
  bad line costs its volume a link and not the whole card. `ApiUnreachable` covers a network error and a
  non-2xx status alike; the page shows one banner over the last good list.
  There is no age-based staleness check — every response is computed live
  from the Kubernetes API, so there is nothing that can go stale the way a
  stored status document would.
- **States.** A `VolumeView.state` is `pending`, `active`, `done`, `failed`
  or `unknown` — computed by the API from the Job's index sets, not stored
  anywhere; a `failed` row's `reason` is `{stage, permanent, error}` parsed
  by the API out of the wrapper's termination message (`stage`/`permanent`
  `null` when it was not the wrapper's JSON), present only while a pod for
  that index still exists.
- **A volume line** — the folded card's strip and every table row — reads the
  same way, and is laid out as three fixed grid tracks rather than a flex row
  so that nothing in it drifts between cards: the **id**, linked to the
  viewer, in the one flexible track; the **two icon links** in a track of
  their own, so two glyphs sit at the same x on ten cards whose ids differ in
  length; then the **status column**, right-aligned at the far end so the
  figures of every row line up under each other the way zone 2's columns do.
  Inside it the **figures come first and the state pill last**, hard against
  the line's right edge: the pill is the fixed-width element, so it is the
  one that can anchor that edge on every row while the variable-width
  figures run up to it. The state word holds a slot the width of the longest
  one ("pending"/"unknown") and the figures hold one wide enough for
  "637 / 638 · 1 failed" in tabular figures, so neither the pill nor the
  line's right edge moves when a poll changes the digits. The
  strip's volume is the one most likely to be wanted (`latest`: newest
  active, else newest done, computed by the API over every volume), so the
  viewer and the run log are one click away without unfolding. It wraps at
  narrow widths like the rest of the card, the status still right-aligned.
- **The links.** The id itself opens the volume in the viewer — its published
  `iiifUrl` once there is one, its source manifest before that (`openHref`).
  A volume with neither is plain text with a title saying there is nothing to
  open yet, never a link that goes nowhere. Beside it two small icon links in
  fixed slots: the volume's **source manifest** when it has one (an `images:`
  volume does not, and the slot stays empty so the row beside it does not
  shift), then its **run log**. Each is labelled "manifest for `<id>`" /
  "run log for `<id>`" and carries the same text as its title; the glyphs
  themselves are inline SVG marked `aria-hidden`, drawn in `currentColor` so
  both themes get them for free, with 24px of hit area and the same focus
  ring every other control on the card wears. Inline, because the page's CSP
  fetches no asset and runs no third-party script — a glyph font would be
  both. One snippet builds them for the strip and the table alike, so the two
  cannot drift.
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
  campaign header adds the API's summed `pagesDone`/`pagesTotal`, which are
  every run volume's once the API has read them all; until then the pages
  row says "counted in N of M volumes" (`pagesCoverage`).
- **Running motion.** Only what is running moves, so that a campaign still
  working cannot be mistaken for a finished one between polls: the state and
  phase chips carry a pulsing dot (`aria-hidden` — the chip's word is the
  state; the header's needs a succeeded warm-up too, since `Running` is also
  what a campaign reads as while its warm-up is pending), an active row and a
  Running header whose summed `pagesTotal` is above zero carry a 3px
  `role="progressbar"` bar whose fill eases to `done`/`total` over 600 ms.
  Zone 2's bars are on **every** card whose total is above zero, finished or
  not — what the phase gates is the sheen, which crosses a bar only while the
  work behind it is actually happening; a finished campaign's bars are still
  and a campaign that ended with pages missing paints them amber. The
  progress line whose `done` actually changed since the last poll fades a
  second of the running blue out from behind its text. Under
  `prefers-reduced-motion: reduce` there is no pulse, no sheen and no fade —
  a bar still shows the same fraction, it just jumps to it.
### The shape of a campaign card

Every card has the same shape, in the same order, because the page's real job
is a list: a reader scanning ten campaigns should find each fact in the same
place on each one, the way they would in a table.

1. **Identity and state**, one line. The left accent bar, the campaign's
   `namespace/name` as the fold toggle, then the phase chip (with its pulsing
   dot while the campaign runs), the warm-up chip while the warm-up has not
   succeeded, and the "job removed" chip. At the right end of the same line,
   when the campaign was created and when it finished — `14 Sept, 10:56 →
   11:00`. The arrow carries the "and then"; the words "created" and
   "finished" are still there for a screen reader, since an arrow is
   decoration to one. A run that finished the same day gives its end the
   clock only. Only a campaign that is **Running** gets the open-ended form
   (`→ …`, "still running"): one that has not started has nothing on the
   other side of the arrow, and a `Queued`, `Paused` or `Unknown` campaign
   shows its created date alone. Both halves stay `<time>` elements carrying
   the exact timestamp in `datetime` and `title`.

   The two partial endings are told apart at a glance, not only by their
   words. **partially succeeded** (every volume finished, some pages lost)
   and **partially failed** (whole volumes lost) share the chip's pale
   amber fill and amber text. The chip's outline is split hard down the
   middle, amber on the left half and green (`--success`) or red
   (`--destructive`) on the right. It is a hard split on the edge rather
   than a blend under the word, so the text's contrast is the plain amber
   chip's in both themes. It is an outline rather than a two-colour dot
   because the dot on this chip already means "running". The card's left
   accent runs from amber at the top to the same green or red at the
   bottom. Both come from the theme tokens, so dark mode follows, and the
   words, the tooltip and the screen-reader sentence stay as they were:
   the colour is a second cue, never the only one.
2. **The body: one grid.** Everything the card counts is a row of the *same*
   four tracks, declared once as CSS custom properties on the card and
   repeated by every row, so they coincide exactly:

   | track | holds |
   | --- | --- |
   | 1 (`minmax(6rem, 1fr)`) | `volumes` / `pages`, or a volume's id linked to the viewer — the words of the row, and the one flexible track, which is what keeps the three fixed things packed against the right edge |
   | 2 (`--icons`) | the two icon links; empty on a totals row |
   | 3 (`--bar`) | the 3px progress bar, short and fixed |
   | 4 (`--fraction`) | the bare fraction `X / Y`, right-aligned and tabular — one column of numbers for the whole card |
   | 5 (`--pill`) | the state pill; empty on a totals row, but the track still holds the column open |

   A row that lost something carries a **second line under its own bar**, in
   the bar's column and right-aligned to it: `1 failed`, or
   `3 failed · 2 errors` on the pages total. A clean row is one line, so
   nothing moves when there is nothing wrong. `· N active` stays beside the
   label on the volumes total: it is not a loss.

   The rows are the campaign's **totals** (`volumes`, `pages`), then the
   **problems line** across all five tracks, then the campaign's **volumes** —
   one row while the card is folded (`latest`: newest active, else newest
   done, computed by the API over every volume), or the whole loaded page as
   an ARIA table when it is open, its column headers present but not drawn.
   So every fraction on a card sits in one column, every bar in another and
   every pill in a third, on every card. A totals row leaves the icons and
   the pill empty and keeps their columns.

   **The fraction** is `2 / 3`, or an em dash when the total is not known
   yet: the API reads page counts out of the bucket, and a campaign that has
   not run has nothing there. `errors` counts ERROR-and-worse only — the
   wrapper's own benign WARNINGs must not read as something wrong on a
   healthy run — and has no column of its own: a count with no fraction
   beside it lined up with nothing.

   **A campaign of one volume drops both totals rows**: that volume's own row
   already carries the same two fractions, and stacking a total over an
   identical row said everything twice. They come back if there is no volume
   row to carry them — a detail that has not loaded yet.

   **Every row with a known total carries a bar**, in the colour of the thing
   it measures: the campaign's two totals take the running blue while it
   runs and amber when it finished with pages missing, and a volume's takes
   its own state's — blue and sheening while it works, green when it
   finished clean, amber when it finished without some of its pages, red
   when it failed. A `pending` or `unknown` volume leaves the track empty: a
   bar of nothing over nothing says less than no bar.

   **The state pill** is the fixed-width element, which is why it ends the
   row: the state word holds a slot the width of the longest one
   ("pending"/"unknown"), so the pill anchors the right edge while the
   figures run up to it. The word is coloured by that volume's health —
   `done` green, `failed` red, `active` blue, `pending`/`unknown` muted, and
   a `done` volume whose `progress.failed` is above zero takes the **warning
   colour**, with "done with N failed pages" as its title and as what a
   screen reader reads so the colour is never the only thing carrying it.
   `describeProgress` adds only what the numbers cannot — the stage, and how
   long ago **while that can still change** ("processing pages · updated 12 s
   ago"): a clock on a volume that is done or failed is one nobody is waiting
   on.

   **What a volume has to say for itself** — why it failed, and the page
   error it reported — is a second line under its own row, spanning the whole
   width and wrapping rather than clipping. A page error is one volume's (it is that
   volume's `progress.lastError`), so it belongs there rather than in a line
   about the campaign; the campaign's problems line keeps it only when the
   volume it happened in is not on screen to say it under. On the folded row
   a failed volume's sentence sits with its id instead, beside it when it
   fits and on a line of its own when it does not.

   At phone width (≤520px) the tracks fold — the id and what failed in it
   across the first line, then the icons, the short bar, the fraction and the
   pill on the second — so it is still one column system. The words wrap
   rather than clip there (on the live phone the figures read "5 / 6 · 1 f"
   and the bar ran on under the icons), and the bar's track may shrink
   between a floor and its full width: line 2's fixed tracks are wider than a
   390px card, and a squeezed grid takes the width back from whichever item
   can give it — which, on a volume row, was the bar, the one cell whose
   content has no width of its own. It collapsed to nothing while the totals
   rows, whose icon and pill cells are empty and could give instead, kept
   theirs.
3. **Problems**, across the grid, and only when there is one: why
   the warm-up could not run, and each failed volume as `id: sentence` with
   the id linking to that volume's run log. The most recent page error joins
   it only when the volume it happened in is not one of the rows on screen —
   otherwise it sits under that row (above), with a link to that volume's run
   log (the API sends its `logUrl`, since the row it happened in is usually
   outside the page being shown). It carries sentences and nothing else — the
   counts are the totals' job and are not repeated here. Warning colour, and
   it **wraps**: clipped to one line, the second failure on could not be read
   on a phone or from a keyboard, and a Tab could land on a link inside the
   clip that nobody could see. Past three sentences the rest wait behind a
   "N more" button (`aria-expanded`, `aria-controls` on the line) rather than
   burying the volumes under a paragraph; the sentences held back are not
   rendered at all, so there is no hidden link to focus. The page error's
   "log" link shows only while its sentence is one of those shown. The id
   and the one-line label cell still cut an over-long id, but with `overflow:
   clip` and a clip margin, so a focused link's ring is drawn whole.
   With the card open it drops the failed volumes that are already visible as
   rows and keeps the rest. `describeLastError` names the failing page once:
   the wrapper writes it into its own message as often as not, and the API
   sends it beside the message, which read "page 0044: page 0044: …" until
   this was fixed.
4. **Provenance**, the card's footer — *below* the volumes, not in the
   header: "pipeline `e2e-vd` · Models: …". Which recipe and which weights
   produced these results is checked once and read least, and in the header
   it competed with the campaign's state for the same row. The pipeline chip
   is still the button that toggles the pipeline's YAML, which opens under
   it.
- **The links.** The id itself opens the volume in the viewer — its published
  `iiifUrl` once there is one, its source manifest before that (`openHref`).
  A volume with neither is plain text with a title saying so, never a link
  that goes nowhere ("nothing to open there yet" only while a volume may
  still publish one; a failed or unrecorded volume says "no viewer manifest
  for this volume"). Beside it two small icon links in fixed slots: the
  volume's **source manifest** when it has one (an `images:` volume does not,
  and the slot stays empty so the row beside it does not shift), then its
  **run log**. Each is labelled "manifest for `<id>`" / "run log for `<id>`" and
  carries the same text as its title; the glyphs themselves are inline SVG
  marked `aria-hidden`, drawn in `currentColor` so both themes get them for
  free, with 24px of hit area and the same focus ring every other control on
  the card wears. Inline, because the page's CSP fetches no asset and runs no
  third-party script — a glyph font would be both. One snippet builds them
  for the folded row and the open list alike, so the two cannot drift.
- **Where each link goes.** Every volume row — and the folded card's latest
  strip — is built by one snippet, so a missing link leaves a gap instead of
  shifting its neighbours.
  - **the id** — `uv.html#?manifest=<url>`: the published `iiifUrl` once the
    volume is `done` or `progress.viewerPublished`, the volume's own
    `sourceUrl` before that, so the viewer is reachable from the first tick.
    Plain text with a title when there is neither.
  - **the manifest icon** — `VolumeView.sourceUrl`, the URL half of the
    volume's `volumes.txt` line, straight to the source manifest. Its slot
    stays empty for an `images:` volume, which lists bare image URLs and has
    no manifest, and for anything that is not an absolute http(s) URL:
    `volumes.txt` is a file humans edit in a git repo, so `isHttpUrl` guards
    it again at the last step before it becomes an href (this also gates the
    id's fallback).
- **Pipeline chip.** In the card's footer beside the models. A button once
  the detail has loaded: its `title` is
  `JobDetail.pipelineSteps` joined by ` → `, and clicking it toggles
  `JobDetail.pipelineYaml` in an inline `<pre>` (`aria-expanded` /
  `aria-controls`). Both fields come from the `htr-pipeline-<id>` ConfigMap;
  when it is gone the chip stays a static label with nothing to toggle.
- **Models line.** The right half of zone 4, the small muted row at the foot
  of the card: one link per model the pipeline loads, in step order,
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
  forgets. While folded it still shows the problems line and one volume row:
  `JobDetail.latest` — the newest `active` volume, else the
  newest `done` one, else nothing — with the same links and the same columns
  every open row has, so the viewer and the run log stay one click away.
  The API computes it over **every** volume,
  like `failures` and unlike `volumes`: picking it in the browser would only
  ever see the page that happens to be loaded, and for a campaign of
  thousands the index in flight is never in the first 200.
- **No thumbnails.** The read API has no per-volume image field; a volume row
  is the card body's four tracks and nothing else.
- **Failures.** `JobDetail.failures` (up to 50 newest failed-with-a-reason
  rows, computed over every volume, independent of the volume list's paging)
  feeds the problems line above the volumes. It carries only the failures the
  reader cannot already see: folded, that is all of them; with the list open,
  it drops every failure whose row is on a loaded page, so a failure is never
  said twice. Nothing is rendered at all when that filtered list is empty.
  Each entry reads `<id>: <sentence>` from `describeReason`, the id linking
  to the same `logHref` its volume row does.
- **Paged volumes.** `CampaignCard` fetches its own volumes via `fetchJob`
  (`offset`/`limit`, default page 200), independently of the campaign list
  poll on `/`; a "load more" button pages in the next batch when
  `counts.total` exceeds what has loaded. A poll re-fetches **every page
  currently open** (`offset 0`, the limit rounded up to whole pages and
  capped at the API's own `limit` ceiling of 1000, rows past that left as
  last fetched), so a tick never undoes "load more". Only a campaign that
  can still change is polled: a `Succeeded`, `Failed`, `PartiallyFailed` or
  `Unknown` campaign, or one whose Job is removed, is read once — retried on
  the poll's backoff until that one read lands — and then left alone, since
  every detail call lists pods and reads ConfigMaps and progress files. That
  one read waits until the card has been on screen (an
  `IntersectionObserver`, with a small margin) or opened; a browser without
  one reads it at once. A change of phase, or the Job being removed, reads
  it once more.
- **Older campaigns.** `fetchJobs` asks for `?reaped=20`: every live
  campaign, and the newest 20 whose Jobs are gone. The API's
  `X-Reaped-Total` header says how many of those there are, and the rest
  wait behind a "show older campaigns" button that asks for 20 more at a
  time.
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
