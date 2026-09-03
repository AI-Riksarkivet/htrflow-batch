# Campaign Browser

The SvelteKit SPA served at `/` by the web image — two routes, no server,
reading the read API (`packages/web`) that serves it. Source:
[`frontend/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/frontend);
the `frontend/README.md` there is the developer-facing version of this page.

- `/` — every campaign (one card per Indexed Job) with its **volume table**
  (id, state chip, links), the pipeline chip, phase and counts in the
  header, and an "API unreachable" banner over the last good list on a
  failed poll. Each card fetches its own volumes, paged.
- `/log?log=<url>&manifest=<url>[&live=1]` — the **run viewer**: the
  wrapper's run log grouped by stage, plus a summary card from
  `manifest.json` (ok/failed/skipped counts, total + wall, median/p95/max,
  the five slowest pages, failed pages with their errors, one cell per page
  coloured by status and scaled by seconds, and the full table behind
  `<details>` in slices of 100 — readable at 480 pages). With `live=1` it
  re-fetches on the wrapper's log-ship cadence and stops on the terminal
  line, a manifest that covers every page, or after 20 failed polls.

## Stack

Svelte 5 (runes) + SvelteKit 2 with `adapter-static` (`prerender = true`,
`ssr = false` — a pure static shell, data fetched in the browser), strict
TypeScript, Zod at the boundary, Vitest (+ @testing-library/svelte on
jsdom), Prettier, Bun as the package runner (`engines`: Node ≥ 22, Bun ≥ 1.1).

| File                                                 | Description                                                                                                                                                 |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/config.ts`                                  | API base and cadence resolution (table below)                                                                                                               |
| `src/lib/api.ts`                                     | Read-API Zod schemas (`JobSummary`, `JobDetail`, `VolumeView`), `fetchJobs`/`fetchJob`, `ApiUnreachable`, and the pure view helpers `isHttpUrl`/`shortDate` |
| `src/lib/run.ts`, `runlog.ts`                        | `manifest.json` schema + summary math (incl. `scale()`, the page grid's bar height); run-log grouping and the terminal-line check                            |
| `src/lib/theme.svelte.ts`                            | the one theme store (`ThemeToggle.svelte` on both routes)                                                                                                   |
| `src/lib/components/`                                | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle`                                                                                   |
| `src/routes/+page.svelte`, `routes/log/+page.svelte` | the two routes                                                                                                                                              |
| `src/app.css`                                        | design tokens per theme (AA-checked), reduced-motion                                                                                                        |
| `static/config.js`                                   | the deployment hook (`window.API_BASE`)                                                                                                                     |

## Configuration

| Setting                | Runtime (deploy)                                        | Build time       | Default                                    |
| ---------------------- | ------------------------------------------------------- | ---------------- | ------------------------------------------ |
| read API base          | `window.API_BASE` — set by **overwriting `/config.js`** | `VITE_API_BASE`  | `/api/v1`                                  |
| campaign list re-fetch | —                                                       | `VITE_RELOAD_MS` | `60000`                                    |
| live-log re-fetch      | —                                                       | `VITE_LIVE_MS`   | `15000` (the wrapper's `LOG_SHIP_SECONDS`) |
| live-log give-up       | —                                                       | —                | `LIVE_MAX_FAILURES = 20` polls (5 min)     |

The API base is resolved on every fetch, highest first: `window.API_BASE`,
then `VITE_API_BASE`, then the default. **The page ships a CSP**
(`svelte.config.js`, `kit.csp` in `hash` mode: `script-src 'self'` plus the
hash of SvelteKit's own init script, `object-src 'none'`, `base-uri 'self'`),
so a deployment sets `window.API_BASE` by serving its own **`/config.js`**
— a same-origin file loaded before the app — never by injecting an inline
`<script>` into `index.html`, which the CSP blocks. `static/config.js`,
built into the image, ships `window.API_BASE = "/api/v1"` — same-origin,
because the read API is the process serving the page, so `script-src 'self'`
already covers it (no `connect-src` directive is set, so fetches are
unrestricted by this CSP; the only restriction is on what may _execute_ as
script). A CSP header from the server must not be stricter than the meta tag
(the browser enforces the intersection); `packages/web` only adds
`frame-ancestors 'none'`.

## Derivation rules

- **Fail-hard parsing.** The read API is ours, not an untrusted document:
  `src/lib/api.ts` parses every response with Zod's `.parse` (not
  `.safeParse`), so a malformed shape throws instead of degrading a row.
  `ApiUnreachable` covers a network error and a non-2xx status alike; the
  page shows one "API unreachable" banner over the last good list. There is
  no age-based staleness check — every response is computed live from the
  Kubernetes API, so there is nothing that can go stale the way a
  reconciler-written document could.
- **States.** A `VolumeView.state` is `pending`, `active`, `done`, or
  `failed` — computed by the API from the Job's index sets, not stored
  anywhere; a `failed` row's `reason` is the wrapper's own termination
  message, present only while a pod for that index still exists.
- **Phase.** A campaign's `JobSummary.phase` (`Queued`/`Paused`/`Running`/
  `Succeeded`/`PartiallyFailed`/`Failed`) drives the card's left accent: red
  if `Failed`, `PartiallyFailed` or any volume is `failed`, blue if
  `Running`, green if `Succeeded`, grey otherwise. `PartiallyFailed` — the
  Job gave up with some indexes already published — shows as "partially
  failed" in the warning colour, not the error colour: part of the campaign
  did come out.
- **Log link** —
  `log?log=<encodeURIComponent(logUrl)>&manifest=<encodeURIComponent(manifestUrl)>`,
  plus `&live=1` for a volume whose `state` is not `"done"`. Both URLs come
  off the same `VolumeView` row: `manifestUrl` is what feeds `/log`'s
  `RunSummaryCard`, and `logUrl` is absolute and bucket-rooted
  (`<public_results_base>/status/logs/<pipeline>/<id>.txt`, no
  namespace/S3_PREFIX prefix): the browser has no bucket base URL to resolve
  a bare key against, so the API builds the full URL — see
  [Live Run Log](../how-it-works/live-run-log.md).
- **Three link slots.** Every volume row — and the folded card's latest
  strip — renders the same three fixed slots, **open · source · log**, from
  one snippet, so a missing link leaves a gap instead of shifting its
  neighbours and the eye can scan a column of "source" straight down.
  - **open** — `uv.html#?manifest=<url>`: the published `iiifUrl` once the
    volume is `done`, the volume's own `sourceUrl` before that, so the
    viewer is reachable from the first tick. Empty when there is neither.
  - **source** — `VolumeView.sourceUrl`, the URL half of the volume's
    `volumes.txt` line, straight to the source manifest. Empty for an
    `images:` volume, which lists bare image URLs and has no manifest.
- **Pipeline chip.** A button once the detail has loaded: its `title` is
  `JobDetail.pipelineSteps` joined by ` → `, and clicking it toggles
  `JobDetail.pipelineYaml` in an inline `<pre>` (`aria-expanded` /
  `aria-controls`). Both fields come from the `htr-pipeline-<id>` ConfigMap;
  when it is gone the chip stays a static label with nothing to toggle.
- **Folded by default.** A card starts collapsed and remembers the reader's
  choice in `localStorage` under `htrflow.card.<namespace>/<name>` — every
  access wrapped, since a browser may refuse storage; the card then simply
  forgets. While folded it still shows the failures block and a one-line
  **latest strip**: the newest `active` volume, else the newest `done` one,
  with the same three link slots, so UV and the run log stay one click away.
- **No thumbnails.** The read API has no per-volume image field; the volume
  table is id / state / links only.
- **Failures block.** `JobDetail.failures` (up to 50 newest
  failed-with-a-reason rows, independent of the volume table's paging) is
  rendered as a compact callout above the volume table, visible even while
  the table is collapsed, only when non-empty: a `failures (<n>)` heading
  and one line per entry, `<id> — <reason>` (reason CSS-clamped to one line,
  no JS truncation), each line linking to the same `logHref` as its table
  row.
- **Paged volumes.** `CampaignCard` fetches its own volumes via `fetchJob`
  (`offset`/`limit`, default page 200), independently of the campaign list
  poll on `/`; a "load more" button pages in the next batch when
  `counts.total` exceeds what has loaded. A poll re-fetches **every page
  currently open** (`offset 0`, the limit rounded up to whole pages and
  capped at the API's own `limit` ceiling of 1000, rows past that left as
  last fetched), so a tick never undoes "load more".
- **Accessibility** — campaign header is a disclosure button; AA contrast in
  both themes; `prefers-reduced-motion` honoured; no horizontal overflow at
  390 px.

## Commands

```bash
cd frontend
bun install
bun run dev        # http://localhost:5173; static/ is served at /
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
