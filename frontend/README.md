# Campaign browser

SvelteKit 2 + Svelte 5 static SPA over the read API (`packages/web`,
`GET /api/v1/jobs`). Three routes, no server:

- `/` — every campaign (one row per Indexed Job) as a card: pipeline chip,
  warm-up chip, phase, counts, and its volume table (id, state, links),
  fetched and paged separately from the list. Cards start folded and show a
  one-line latest strip while they are. An "API unreachable" banner on top
  of the last good list when a poll fails.
- `/log?log=<url>&manifest=<url>[&live=1]` — the run viewer: the wrapper's
  run log grouped by stage, plus a summary card (counts, median / p95 / max,
  slowest pages, failed pages, a per-page grid) from `manifest.json`. With
  `live=1` it re-fetches on the wrapper's log-ship cadence and stops on the
  terminal line, a finished manifest, or after `LIVE_MAX_FAILURES` misses.
- `/alto?src=<url>` — the ALTO viewer: one page's ALTO XML as text in
  reading order, each line tinted by its `WC` confidence, with a raw-XML
  toggle. Reached from the run viewer's alto column.

`bun run build` emits `dist/`, which `.docker/htrflow-web.dockerfile` copies
into the read API's `/app/static` (over the Universal Viewer build, so `/` is
this SPA and `/uv.html` is UV). One image, one origin: the API the browser
talks to is the process serving the page.

## Commands

```bash
bun install
bun run dev        # http://localhost:5173, LAN-reachable; static/ is served at /
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

| Setting                | Runtime (deploy)                           | Build time       | Default                                    |
| ---------------------- | ------------------------------------------ | ---------------- | ------------------------------------------ |
| read API base          | `window.API_BASE` — overwrite `/config.js` | `VITE_API_BASE`  | `/api/v1`                                  |
| campaign list re-fetch | —                                          | `VITE_RELOAD_MS` | `60000`                                    |
| live-log re-fetch      | —                                          | `VITE_LIVE_MS`   | `15000` (the wrapper's `LOG_SHIP_SECONDS`) |
| live-log give-up       | —                                          | —                | `LIVE_MAX_FAILURES = 20` polls (5 min)     |

The API base is resolved on every fetch, highest first: `window.API_BASE`,
then `VITE_API_BASE`, then the default. The page carries a CSP
(`script-src 'self'` plus the hash of SvelteKit's own init script, see
`svelte.config.js`), so a deployment sets `window.API_BASE` by **overwriting
`/config.js`** — a same-origin file loaded before the app — never by
injecting an inline `<script>` into `index.html`. `static/config.js` ships
`window.API_BASE = "/api/v1"`, which is same-origin because the read API
serves this page — so the default `script-src 'self'` covers it with no
`connect-src` change needed. The server's own CSP header
(`frame-ancestors 'none'`, from `packages/web`) must not be stricter than
the meta tag: the browser enforces the intersection.

## The read API

`src/lib/api.ts` is the boundary: Zod schemas for `JobSummary`/`JobDetail`/
`VolumeView`, and `fetchJobs()` / `fetchJob(namespace, name, offset, limit)`.
This is our own API, not a document we found — a malformed response is a bug
on our side, so parsing **fails hard** (`.parse`, not `.safeParse`): no
per-row degrading. `ApiUnreachable` covers both a network error and a
non-2xx status; the page shows one "API unreachable" banner over the last
good list. There is no staleness check: every response is computed live from
the Kubernetes API, so there is nothing that can go stale.

```jsonc
// GET /api/v1/jobs — JobSummary[]
{
  "namespace": "htr-test",
  "name": "kyrk",
  "pipeline": "demo-v1",
  "phase": "Running", // Succeeded | PartiallyFailed | Failed | Queued | Paused | Running
  "counts": { "total": 7, "active": 1, "done": 4, "failed": 1 },
  "suspended": false,
  "createdAt": "2026-01-01T00:00:00Z",
  "resultsBase": "https://results.example.org/htr-test/demo-v1",
  "warmup": { "phase": "succeeded" }, // missing | pending | running | succeeded
  //                                  // | failed (then also `reason`)
}
```

```jsonc
// GET /api/v1/jobs/{namespace}/{name}?offset=0&limit=200 — JobSummary + this
{
  "pipelineSteps": ["Segmentation", "TextRecognition"], // the chip's tooltip
  "pipelineYaml": "steps:\n  - step: Segmentation\n…", // the chip's toggle
  "latest": {/* the VolumeView a folded card shows, or null */},
  "failures": [/* up to 50 most recent failed-with-a-reason VolumeView rows */],
  "volumes": [
    {
      "index": 3,
      "id": "vol3",
      "state": "failed", // pending | active | done | failed
      "manifestUrl": "https://…/vol3/manifest.json",
      "iiifUrl": "https://…/vol3/iiif.json",
      "altoPrefix": "https://…/vol3/alto/",
      "sourceUrl": "https://iiif.example.org/vol3/manifest", // null for `images:`
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

`CampaignCard.svelte` fetches its own volumes via `fetchJob`, paged by
`offset`/`limit` (a "load more" button pages in the next `limit` rows), on
its own `RELOAD_MS` timer — independent of the list poll on `/`. `logUrl` is
absolute and bucket-rooted (no namespace/S3_PREFIX prefix): the browser has
no bucket base URL to resolve a bare key against, so the API builds it. The
volume table's `log` link is
`log?log=<encodeURIComponent(logUrl)>&manifest=<encodeURIComponent(manifestUrl)>`,
plus `&live=1` when `state !== "done"` — `manifestUrl` (same `VolumeView`
row) is what feeds `/log`'s `RunSummaryCard`. `JobDetail.failures` (up to 50
newest failed-with-a-reason rows) renders as a compact callout above the
table, visible even while it's collapsed, only when non-empty — one line per
entry (`<id> — <reason>`, reason clamped to one line by CSS, no JS
truncation), each line linking to the same log href as its table row.

## Layout

| File                                       | What                                                                                    |
| ------------------------------------------ | --------------------------------------------------------------------------------------- |
| `src/lib/config.ts`                        | API base and cadence resolution (table above)                                           |
| `src/lib/api.ts`                           | Read-API Zod schemas, `fetchJobs`/`fetchJob`, `ApiUnreachable`, `isHttpUrl`/`shortDate` |
| `src/lib/run.ts`, `runlog.ts`              | `manifest.json` schema + summary math (incl. each page's `alto` URL); run-log grouping  |
| `src/lib/alto.ts`                          | `parseAlto` (ALTO XML → text lines + confidence), `altoUrl`, `prettyXml`                |
| `src/lib/reasons.ts`                       | the one place a `reason` or a fetch failure becomes a sentence a person reads           |
| `src/lib/theme.svelte.ts`                  | the one theme store (`ThemeToggle.svelte` on every route)                               |
| `src/lib/components/`                      | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle`               |
| `src/routes/+page.svelte`, `log/`, `alto/` | the three routes                                                                        |
| `src/app.css`                              | design tokens per theme (AA-checked), reduced-motion, the chrome shared by every route  |
| `static/config.js`                         | the deployment hook (`window.API_BASE`, `/api/v1` by default)                           |

Tests sit next to their subject (`*.test.ts`); component tests use
@testing-library/svelte + user-event on jsdom, route tests mock `fetch` and
fake timers.
