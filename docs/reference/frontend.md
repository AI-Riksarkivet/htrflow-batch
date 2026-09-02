# Campaign Browser

The SvelteKit SPA served at `/` by the viewer image — two routes, no server,
reading the read API (`packages/web`) directly. Source:
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
| `src/lib/run.ts`, `runlog.ts`                        | `manifest.json` schema + summary math; run-log grouping and the terminal-line check                                                                         |
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
`<script>` into `index.html`, which the CSP blocks. The chart does exactly
that: `templates/viewer.yaml` mounts a ConfigMap-rendered `config.js` with
`window.API_BASE = "/api/v1"`, and its nginx proxies `/api/` to the read
API's Service — same-origin, so `script-src 'self'` already covers it (no
`connect-src` directive is set, so fetches are unrestricted by this CSP; the
only restriction is on what may _execute_ as script). A CSP header from the
web server must not be stricter than the meta tag (the browser enforces the
intersection); the chart's nginx only adds `frame-ancestors 'none'`.

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
  `Succeeded`/`Failed`) drives the card's left accent: red if `Failed` or
  any volume is `failed`, blue if `Running`, green if `Succeeded`, grey
  otherwise.
- **Log link** —
  `log?log=<encodeURIComponent(logUrl)>&manifest=<encodeURIComponent(manifestUrl)>`,
  plus `&live=1` for a volume whose `state` is not `"done"`. Both URLs come
  off the same `VolumeView` row: `manifestUrl` is what feeds `/log`'s
  `RunSummaryCard`, and `logUrl` is absolute and bucket-rooted
  (`<public_results_base>/status/logs/<pipeline>/<id>.txt`, no
  namespace/S3_PREFIX prefix): the browser has no bucket base URL to resolve
  a bare key against, so the API builds the full URL — see
  [Live Run Log](../how-it-works/live-run-log.md).
- **Open link** — a `done` volume links `iiifUrl` as
  `uv.html#?manifest=<url>`; other states get no open link (there is no
  separate pre-run "source manifest" any more — the API only returns
  results-side URLs).
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
  `counts.total` exceeds what has loaded.
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
bun run build      # static build → dist/, consumed by .docker/uv4-viewer.dockerfile
```

The viewer image (`.docker/uv4-viewer.dockerfile`,
`nginxinc/nginx-unprivileged`, port 8080) serves this build at `/` and
Universal Viewer at `/uv.html`; `make viewer-image` stages `dist/` into the
UV checkout and builds it.
