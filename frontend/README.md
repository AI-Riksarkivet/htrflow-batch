# Campaign browser

SvelteKit 2 + Svelte 5 static SPA over the read API (`packages/web`,
`GET /api/v1/jobs`). Three routes, no server:

- `/` — every campaign (one row per Indexed Job) as a card: pipeline chip,
  warm-up chip, phase, counts, and its volume table (id, state, links),
  fetched and paged separately from the list. Cards start folded, showing
  only their header line. An "API unreachable" banner on top of the last
  good list when a poll fails.
- `/log?log=<url>&manifest=<url>[&live=1]` — the run viewer: the wrapper's
  run log grouped by stage, plus a summary card (counts, median / p95 / max,
  slowest pages, failed pages, a per-page grid) from `manifest.json`. With
  `live=1` it re-fetches on the wrapper's log-ship cadence and stops on the
  terminal line, a finished manifest, or after `LIVE_MAX_FAILURES` misses.
- `/alto?src=<url>` — the ALTO viewer: one page's ALTO XML as text in
  reading order, each line tinted by its `WC` confidence, with a raw-XML
  toggle. Reached from the run viewer's alto column; like `/log`, it reads
  only a URL under the results base.

`bun run build` emits `dist/`, which `.docker/htrflow-web.dockerfile` copies
into the read API's `/app/static` (over the Universal Viewer build, so `/` is
this SPA and `/uv.html` is UV). One image, one origin: the API the browser
talks to is the process serving the page.

## Commands

```bash
bun install
bun run dev        # Vite dev server on :5173, LAN-reachable; static/ is served at /
bun run test       # vitest (pure + component tests, jsdom)
bun run coverage   # vitest with @vitest/coverage-v8
bun run check      # svelte-check, strict TypeScript
bun run lint       # prettier --check (prettier-plugin-svelte)
bun run format     # prettier --write
bun run build      # static site → dist/
bunx vite preview  # serve dist/ on :4173
```

`engines` pins Node ≥ 22 and Bun ≥ 1.1.

## Configuration

All of it lives in [`src/lib/config.ts`](src/lib/config.ts).

| Setting                | Runtime (deploy)                              | Build time          | Default                                    |
| ---------------------- | --------------------------------------------- | ------------------- | ------------------------------------------ |
| read API base          | `window.API_BASE`, served in `/config.js`     | `VITE_API_BASE`     | `/api/v1`                                  |
| results base           | `window.RESULTS_BASE`, served in `/config.js` | `VITE_RESULTS_BASE` | _(empty — see below)_                      |
| campaign list re-fetch | —                                             | `VITE_RELOAD_MS`    | `60000`                                    |
| live-log re-fetch      | —                                             | `VITE_LIVE_MS`      | `15000` (the wrapper's `LOG_SHIP_SECONDS`) |
| live-log give-up       | —                                             | —                   | `LIVE_MAX_FAILURES = 20` attempts          |
| poll backoff ceiling   | —                                             | —                   | `MAX_POLL_MS = 300000`                     |

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
`connect-src` directive is in the meta tag). The server adds what the build
cannot know: `packages/web` sends `frame-ancestors 'none'` on every response
and, on the SPA's own pages, `connect-src 'self' <results base>/`, so the page
may fetch only from the API and the results bucket. The browser enforces the
header and the meta tag both, so the header only adds directives. `/uv.html`,
which has no meta tag, gets a policy of its own from `packages/web`.

## The read API

`src/lib/api.ts` is the boundary: Zod schemas for `JobSummary`/`JobDetail`/
`VolumeView`, and `fetchJobs()` / `fetchJob(namespace, name, offset, limit)`.
This is our own API, not a document we found — a response of the wrong shape
is a bug on our side, so parsing **fails hard** (`.parse`). One campaign row
the page cannot read is still a bug, but not a reason to hide the rest:
`fetchJobs` leaves that row out (`.safeParse` per row), counts it, logs the
first issue to the console and shows a banner saying how many campaigns are
hidden; a list whose every row is unreadable throws like a wrong shape. One
field is read on its own: `VolumeView.sourceUrl`, a line of a file people
edit, becomes `null` when it is not an http(s) URL this browser can use
(`httpUrlSchema.catch(null)`), so one bad line costs its volume a link, not
the card. `ApiUnreachable` covers both a network error and a non-2xx status;
the page shows one banner over the last good list. There is no staleness
check: every response is computed live.

```jsonc
// GET /api/v1/jobs?reaped=20 — JobSummary[]: every live campaign, and the
// 20 newest whose Jobs are gone (X-Reaped-Total: how many of those in all)
{
  "namespace": "htr-test",
  "name": "kyrk",
  "pipeline": "demo-v1",
  "phase": "Running", // Succeeded | PartiallyFailed | Failed | Queued | Paused | Running
  //                    // | Unknown (only with jobGone)
  "counts": { "total": 7, "active": 1, "done": 4, "failed": 1 },
  "suspended": false,
  "createdAt": "2026-01-01T00:00:00Z",
  "resultsBase": "https://results.example.org/htr-test/demo-v1",
  "warmup": { "phase": "succeeded" }, // missing | pending | running | succeeded
  //                                  // | failed (then also `reason`)
  "jobGone": false, // true: the Job is past its TTL, the row is its record
}
```

```jsonc
// GET /api/v1/jobs/{namespace}/{name}?offset=0&limit=200 — JobSummary + this
{
  "pipelineSteps": ["Segmentation", "TextRecognition"], // the chip's tooltip
  "pipelineYaml": "steps:\n  - step: Segmentation\n…", // the chip's toggle
  "latest": {
    /* the newest active, else done, VolumeView, or null; not drawn */
  },
  "failures": [
    /* up to 50 newest failed VolumeView rows, with a reason or without */
  ],
  "volumes": [
    {
      "index": 3,
      "id": "vol3",
      "state": "failed", // pending | active | done | failed | unknown (jobGone only)
      "manifestUrl": "https://…/vol3/manifest.json",
      "iiifUrl": "https://…/vol3/iiif.json",
      "altoPrefix": "https://…/vol3/alto/",
      "sourceUrl": "https://iiif.example.org/vol3/manifest", // null for `images:`,
      //                                                    // or one the page cannot use
      "logUrl": "https://…/status/logs/demo-v1/vol3.txt", // absolute, always present
      "reason": { "stage": "setup", "permanent": true, "error": "…" },
      // the wrapper's own termination message, parsed; present only while a
      // pod for that index still exists
    },
  ],
}
```

`latest`, `failures`, `pipelineSteps` and `pipelineYaml` are computed over
**every** volume, unaffected by `offset`/`limit` — a campaign of thousands
shows its first 200 rows, and the volume in flight is almost never among
them.

The full field reference, for operators and authors, is
[Web front & read API](../docs/reference/web.md). `logUrl` is
absolute and bucket-rooted (no namespace/S3_PREFIX prefix): the browser has
no bucket base URL to resolve a bare key against, so the API builds it. The
volume table's `log` link is
`log?log=<encodeURIComponent(logUrl)>&manifest=<encodeURIComponent(manifestUrl)>`,
plus `&live=1` when `state !== "done"` — `manifestUrl` (same `VolumeView`
row) is what feeds `/log`'s `RunSummaryCard`.

## The campaign card

`CampaignCard.svelte`. Every card has the same four zones in the same
order, so ten cards scan like ten rows of one table. Folded, a card is zone
1 alone: zones 2 to 4 render only while it is open.

1. **Identity and state**, one line: the left accent bar, the name as the
   fold toggle (`namespace/name` only when the list spans namespaces: the
   page decides it once and passes `showNamespace` to every card; keys,
   storage and API paths are always `namespace/name`), the phase chip (a pulsing dot while running), the
   warm-up chip while the warm-up has not succeeded, the "job removed" chip,
   and at the right end `created → finished` as two `<time>` elements
   (`datetime` and `title` carry the exact timestamp; "created"/"finished"
   are there for screen readers, since the arrow is decoration). A run that
   finished the same day shows only the clock for its end. Only a `Running`
   campaign gets the open-ended `→ …`.
   **partially succeeded** (every volume finished, some pages lost) and
   **partially failed** share the amber chip; the outline is split amber on
   the left and green (`--success`) or red (`--destructive`) on the right,
   and the accent bar runs amber to the same colour. An outline, not a dot,
   because the dot already means "running"; the words, tooltip and
   screen-reader sentence stay the same, so colour is never the only cue.
2. **The body: one grid.** Totals (`volumes`, `pages`), the problems line,
   then the loaded page of volumes as an ARIA table (headers present but not
   drawn). Every row uses the same five
   tracks, declared once as custom properties on the card:

   | track                   | holds                                                                           |
   | ----------------------- | ------------------------------------------------------------------------------- |
   | 1 (`minmax(6rem, 1fr)`) | the row's words or the volume id — the one flexible track                       |
   | 2 (`--icons`)           | the two icon links; empty on a totals row                                       |
   | 3 (`--bar`)             | the 3px progress bar                                                            |
   | 4 (`--fraction`)        | `X / Y`, right-aligned, tabular figures (an em dash when the total is unknown)  |
   | 5 (`--pill`)            | the state pill, fixed width so it anchors the right edge; empty on a totals row |
   - A row that lost something carries a second line under its bar
     (`1 failed`, `3 failed · 2 errors`); a clean row is one line.
     `errors` counts ERROR-and-worse only.
   - A campaign of one volume drops both totals rows, unless there is no
     volume row yet to carry them.
   - Every row with a known total has a bar in the colour of what it
     measures: running blue (with a sheen only while work happens), green
     done, amber done with pages missing, red failed. `pending`/`unknown`
     leave the track empty.
   - The pill word holds a slot as wide as "pending"/"unknown". A `done`
     volume with failed pages takes the warning colour, titled "done with N
     failed pages". `describeProgress` adds the stage and "updated N s ago"
     only while the volume can still change.
   - A volume's failure sentence and page error sit on a second line under
     its own row, wrapping.
   - At ≤520px the tracks fold onto two lines (id and failure first, then
     icons, bar, fraction, pill); words wrap, and the bar may shrink
     between a floor and its full width.

3. **Problems**, across the grid, only when there is one: the warm-up's
   failure, each failed volume as `id: sentence` (the id links to its run
   log), and the latest page error only when its volume is not on screen
   (with a log link from `lastError.logUrl`). Warning colour, wrapping.
   Past three sentences the rest wait behind "N more" (`aria-expanded`,
   `aria-controls`), and are not rendered, so no hidden link takes focus.
   It drops failures already visible as rows.
   `describeLastError` names the failing page once, even when the wrapper's
   message already names it.
4. **Provenance**, the footer: the pipeline chip (a button once the detail
   has loaded: `title` is `pipelineSteps` joined by `→`, click toggles
   `pipelineYaml` in a `<pre>`), and the models line. `src/lib/pipeline.ts`
   reads the YAML line by line for `model_settings.model` and its revision
   (`model_settings.revision` or `model_settings.model_kwargs.revision`,
   never the processor's), rendering `<repo> @<short rev>` or
   `<repo> unpinned`, linked to `https://huggingface.co/<id>/tree/<rev>`.

**Links.** One snippet builds every volume row's links, so a missing link
leaves a gap instead of shifting its neighbours. The id opens
`uv.html#?manifest=<url>`: `iiifUrl` once the volume is `done` or
`progress.viewerPublished`, `sourceUrl` before that, plain text with a title
when there is neither. Beside it, fixed slots for the source-manifest icon
(empty for `images:` volumes) and the run-log icon, labelled "manifest for
`<id>`" / "run log for `<id>`": inline SVG, `aria-hidden`, `currentColor`,
24px hit area — inline because the CSP loads no asset.

**Warm-up chip.** "warm-up pending/running/failed" or "no warm-up"
(`missing`). `failed` pushes the accent to the failed colour; `missing` does
so only while the phase is not `Succeeded`. A failed warm-up's
`describeReason` is the chip's `title` and, when open, a line under it.

**Folding, paging, polling.** Cards start folded and remember the choice in
`localStorage` (`htrflow.card.<namespace>/<name>`, every access wrapped).
`fetchJob` pages by `offset`/`limit` (200); "load more" adds a page, and a
poll re-fetches every open page (capped at the API's 1000). A folded card
reads nothing, except a `Succeeded` one, which reads its detail once for the
one thing its header needs that `JobSummary` lacks: `pagesFailed`, which
makes it "partially succeeded". An open card that can still change is
polled; a finished, `Unknown` or reaped one is read once, when its card
first intersects the viewport (`IntersectionObserver`) or is opened, and
again on a phase change. `fetchJobs` asks for
`?reaped=20`; "show older campaigns" asks for 20 more.

**Motion and accessibility.** Only what runs moves: the pulsing dot, the
bar sheen, and a one-second fade behind a progress line whose `done`
changed. Bars ease to their fraction over 600 ms. Under
`prefers-reduced-motion: reduce` there is no pulse, sheen or fade. The
campaign header is a disclosure button with `aria-controls` only while the
card is open (it names the element holding zones 2 to 4). AA contrast in both themes, no horizontal overflow at
390px.

**Header.** Logo and title left; the deployed release from
`GET /api/v1/version` (`htrflow-batch <version>`, `web` in the tooltip,
read once, absent if it fails), the GitHub mark and the theme toggle right.

## Layout

| File                                       | What                                                                                                  |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `src/lib/config.ts`                        | API base and cadence resolution (table above)                                                         |
| `src/lib/api.ts`                           | Read-API Zod schemas, `fetchJobs`/`fetchJob`, `ApiUnreachable`, `isHttpUrl`/`isResultUrl`/`shortDate` |
| `src/lib/poll.ts`                          | the one poller: one request in flight, paused while hidden, backing off on failures                   |
| `src/lib/run.ts`, `runlog.ts`              | `manifest.json` schema + summary math (incl. each page's `alto` URL); run-log grouping                |
| `src/lib/alto.ts`                          | `parseAlto` (ALTO XML → text lines + confidence), `altoUrl`, `prettyXml`                              |
| `src/lib/pipeline.ts`                      | the models a pipeline YAML names, and the Hugging Face link for each                                  |
| `src/lib/reasons.ts`                       | the one place a `reason` or a fetch failure becomes a sentence a person reads                         |
| `src/lib/theme.svelte.ts`                  | the one theme store (`ThemeToggle.svelte` on every route)                                             |
| `src/lib/components/`                      | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle`                             |
| `src/routes/+page.svelte`, `log/`, `alto/` | the three routes                                                                                      |
| `src/app.css`                              | design tokens per theme (AA-checked), reduced-motion, the chrome shared by every route                |
| `static/config.js`                         | the deployment hook (`window.API_BASE`, `/api/v1` by default)                                         |

Tests sit next to their subject (`*.test.ts`); component tests use
@testing-library/svelte + user-event on jsdom, route tests mock `fetch` and
fake timers.
