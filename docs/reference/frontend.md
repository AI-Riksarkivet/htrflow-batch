# Campaign Browser

The SvelteKit SPA served at `/` by the viewer image — two routes over the
reconciler's `status.json`, no server. Source:
[`frontend/`](https://github.com/carpelan/test/tree/main/frontend); the
`frontend/README.md` there is the developer-facing version of this page.

- `/` — every campaign as a card with its **volume table** (status chip,
  `terminal` tag, pages, attempts, updated, links), the header meta
  (campaigns repo link, generated-at, last tick cost) and the stale / error /
  warning banners.
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

| File | Description |
|------|-------------|
| `src/lib/config.ts` | URL and cadence resolution (table below) |
| `src/lib/status.ts` | Zod schemas mirroring the reconciler's `status.json` ([schema](s3-layout.md#statusjson)) and the fail-soft parser |
| `src/lib/derive.ts` | Pure view derivation: `viewerHref`, `campaignHealth`, `isStale`, `pagesLabel`, `shortDate`, `tickSummaryLabel`, `isHttpUrl` |
| `src/lib/run.ts`, `runlog.ts` | `manifest.json` schema + summary math; run-log grouping and the terminal-line check |
| `src/lib/theme.svelte.ts` | the one theme store (`ThemeToggle.svelte` on both routes) |
| `src/lib/components/` | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle` |
| `src/routes/+page.svelte`, `routes/log/+page.svelte` | the two routes |
| `src/app.css` | design tokens per theme (AA-checked), reduced-motion |
| `static/config.js` | the deployment hook (`window.STATUS_URL`) |
| `static/status.sample.json` | dev fixture — the full current shape, test-guarded |

## Configuration

| Setting | Runtime (deploy) | Build time | Default |
|---|---|---|---|
| status.json URL | `window.STATUS_URL` — set by **overwriting `/config.js`** | `VITE_STATUS_URL` | `http://localhost:30900/htr-results/status/status.json` |
| campaign page re-fetch | — | `VITE_RELOAD_MS` | `60000` |
| live-log re-fetch | — | `VITE_LIVE_MS` | `15000` (the wrapper's `LOG_SHIP_SECONDS`) |
| live-log give-up | — | — | `LIVE_MAX_FAILURES = 20` polls (5 min) |

The status URL is resolved on every fetch, highest first: `window.STATUS_URL`,
then `VITE_STATUS_URL`, then the default. **The page ships a CSP**
(`svelte.config.js`, `kit.csp` in `hash` mode: `script-src 'self'` plus the
hash of SvelteKit's own init script, `object-src 'none'`, `base-uri 'self'`),
so a deployment sets `window.STATUS_URL` by serving its own **`/config.js`**
— a same-origin file loaded before the app — never by injecting an inline
`<script>` into `index.html`, which the CSP blocks. The chart does exactly
that: `templates/viewer.yaml` mounts a ConfigMap-rendered `config.js`
pointing at `<viewer.statusBase | publicResultsBase>/status/status.json`.
A CSP header from the web server must not be stricter than the meta tag
(the browser enforces the intersection); the chart's nginx only adds
`frame-ancestors 'none'`.

## Derivation rules

- **Fail-soft parsing.** The envelope (`generated_at`, `tick_seconds`,
  `warnings`, `campaigns`) must parse; each campaign and each volume is
  parsed on its own, and a bad entry degrades to an error row with a
  warning line under the header while the rest renders. A failed poll keeps
  the last good document on screen under an alert banner.
- **URL fields** (`viewer_manifest`, `run_manifest`, `source_manifest`,
  `thumbnail`, `failure_log`, `run_log`) are accepted only as absolute
  `http(s)` URLs — anything else becomes `null` plus a warning, so it never
  reaches an `href` or `src`. The `/log` route applies the same rule to its
  query parameters.
- **Statuses.** `done`, `running`, `queued`, `retry`, `deleting`,
  `needs-attention`, `pending` (rendered "planned"), `unreachable`,
  `unsupported`; any other value parses as `unknown` with a neutral chip, so
  a newer reconciler cannot blank the page.
- **`terminal` tag.** A non-null `terminal` (`exit-13`, `capped`) shows as a
  tag next to the chip; its title says an operator clears it (the attempts
  record, or a new pipeline id).
- **Viewer link** — a `done` volume links to its published `viewer_manifest`
  (results + ALTO overlays); any other state links to its `source_manifest`
  (the raw scans), both as `uv.html#?manifest=<url>`.
- **Campaign health** (the card's left accent) — worst volume wins: red if
  any volume is `needs-attention`/`unreachable`/`unsupported`, blue if any is
  `running`/`queued`/`retry`/`deleting`, green if every volume is `done`,
  grey otherwise.
- **Run log** — `log` links `run_log` into `/log?log=…&manifest=<run_manifest>`;
  for a volume that is not `done` it adds `live=1`. `failure_log` wins the
  slot for failed volumes.
- **Thumbnails** — `thumbnail` is a sized IIIF URL or `null`; `null` and a
  broken image both render the neutral placeholder square. Images load with
  `fetchpriority="low"`.
- **Tick summary** — `tick_summary` renders as one line in the header
  (`4.1 s · 12 S3 calls · 3 validations · 1 submitted`); every field is
  optional and `{}`/absent hides the line.
- **Stale banner** — shown when `generated_at` is older than
  `3 × tick_seconds` (15 min at the default tick): the reconciler is presumed
  dead, the numbers are historical.
- **Accessibility** — campaign header and pipeline chip are sibling buttons
  (no nested `role=button`); AA contrast in both themes; `prefers-reduced-motion`
  honoured; no horizontal overflow at 390 px.

## Commands

```bash
cd frontend
bun install
bun run dev        # http://localhost:5173; static/ is served at /
bun run test       # vitest (pure + component tests, jsdom) — 76 tests
bun run coverage   # vitest with @vitest/coverage-v8
bun run check      # svelte-check, strict TypeScript
bun run lint       # prettier --check
bun run format     # prettier --write
bun run build      # static build → dist/, consumed by .docker/uv4-viewer.dockerfile
```

To point the dev server at the fixture: `window.STATUS_URL =
"/status.sample.json"` in the console (the next poll picks it up), or put
that line in `static/config.js` while you work (don't commit it).

The viewer image (`.docker/uv4-viewer.dockerfile`,
`nginxinc/nginx-unprivileged`, port 8080) serves this build at `/` and
Universal Viewer at `/uv.html`; `make viewer-image` stages `dist/` into the
UV checkout and builds it (the dev fixture is not shipped).
