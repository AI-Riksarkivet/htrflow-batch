# Campaign browser

SvelteKit 2 + Svelte 5 static SPA over a legacy `status.json` (pre-B63 shape;
migrating onto the read API's `GET /api/v1/jobs` is B63 Task 7). Two
routes, no server:

- `/` — every campaign as a card with its volume table (status, pages,
  attempts, updated, links), the header meta (campaigns repo, generated-at,
  last tick cost) and the stale / error / warning banners.
- `/log?log=<url>&manifest=<url>[&live=1]` — the run viewer: the wrapper's
  run log grouped by stage, plus a summary card (counts, median / p95 / max,
  slowest pages, failed pages, a per-page grid) from `manifest.json`. With
  `live=1` it re-fetches on the wrapper's log-ship cadence and stops on the
  terminal line, a finished manifest, or after `LIVE_MAX_FAILURES` misses.

`bun run build` emits `dist/`, which `.docker/uv4-viewer.dockerfile` serves at
`/` next to Universal Viewer at `/uv.html`.

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

| Setting                | Runtime (deploy)                             | Build time        | Default                                                 |
| ---------------------- | -------------------------------------------- | ----------------- | ------------------------------------------------------- |
| status document URL    | `window.STATUS_URL` — overwrite `/config.js` | `VITE_STATUS_URL` | `http://localhost:30900/htr-results/status/status.json` |
| campaign page re-fetch | —                                            | `VITE_RELOAD_MS`  | `60000`                                                  |
| live-log re-fetch      | —                                            | `VITE_LIVE_MS`    | `15000` (the wrapper's `LOG_SHIP_SECONDS`)              |
| live-log give-up       | —                                            | —                 | `LIVE_MAX_FAILURES = 20` polls (5 min)                  |

The status URL is resolved on every fetch, highest first: `window.STATUS_URL`,
then `VITE_STATUS_URL`, then the default. The page carries a CSP
(`script-src 'self'` plus the hash of SvelteKit's own init script, see
`svelte.config.js`), so a deployment sets `window.STATUS_URL` by **overwriting
`/config.js`** — a same-origin file loaded before the app — never by injecting
an inline `<script>` into `index.html`. A CSP header from the web server must
not be stricter than the meta tag (the browser enforces the intersection).

## Dev fixture

`static/status.sample.json` is the full current `status.json` shape — every
status, a `terminal` volume, a broken campaign, `tick_summary` — and is served
at `/status.sample.json` by `bun run dev`. In the browser console:

```js
window.STATUS_URL = "/status.sample.json";
```

The next poll (within `RELOAD_MS`) picks it up; a reload clears it. To make it
stick, put the line in `static/config.js` while you work (don't commit it).

## status.json

Parsed at the boundary by `src/lib/status.ts` (Zod, "parse, don't validate")
and fail-soft: the envelope must parse, each campaign and each volume is
parsed on its own, and a bad entry degrades to an error row (with a warning
line under the header) while the rest renders. Every URL field is accepted
only as an absolute `http(s)` URL — anything else becomes `null` plus a
warning, so it never reaches an `href` or `src`.

```jsonc
{
  "generated_at": "2026-08-26T08:55:12+00:00",
  "tick_seconds": 300, // stale banner after 3 × this
  "campaigns_repo_url": "https://…", // linked when http(s), else shown as text
  "warnings": ["…"], // one line each under the header
  "tick_summary": {
    // what the last reconcile cost; all optional
    "seconds": 4.06,
    "s3_calls": 12,
    "validations": 3,
    "submitted": 1,
    "retried": 0,
  },
  "campaigns": [
    {
      "name": "demo",
      "pipeline": "demo-v1", // null when the campaign failed to parse
      "pipeline_steps": ["Segmentation: yolo (…)", "…"], // chip tooltip
      "pipeline_yaml": "steps:\n…", // shown by the chip; null → static chip
      "error": null, // set → "broken" card, no table
      "totals": {
        "done": 1,
        "total": 7,
        "pages_done": 722,
        "pages_total": 2610,
      },
      "orphans": ["R0009999"], // results in the bucket, not in git
      "volumes": [
        {
          "id": "R0001203",
          "status": "done", // see the vocabulary below
          "attempts": 0,
          "pages_done": 638,
          "pages_total": 638, // null when unknown
          "error": null, // per-volume text, rendered under the id
          "viewer_manifest": "http://…/iiif.json", // "open" for done volumes
          "run_manifest": "http://…/manifest.json", // run viewer summary card
          "source_manifest": "https://…/manifest", // "source"; required in spirit
          "thumbnail": "https://…/full/200,/0/default.jpg", // sized IIIF or null
          "updated": "2026-08-25T13:29:24Z", // null until published
          "failure_log": null, // wins the "log" slot when set
          "run_log": "http://…/logs/demo-v1/R0001203.txt", // /log?log=…&manifest=…
          "terminal": null, // "exit-13" | "capped": sticky, operator clears
        },
      ],
    },
  ],
}
```

Volume statuses: `done`, `running`, `queued`, `retry`, `deleting` (a retry's
Job under deletion), `needs-attention`, `pending` (rendered "planned"),
`unreachable`, `unsupported`. Any other value parses as `unknown` and gets a
neutral chip, so an unrecognised shape cannot blank the page. Campaign accent
(worst volume wins): red for `needs-attention` / `unreachable` /
`unsupported`, blue for `running` / `queued` / `retry` / `deleting`, green
when every volume is `done`, grey otherwise. A `terminal` volume shows the
reason as a tag next to its chip; hovering explains what clears it.

`manifest.json` (the run viewer) is `src/lib/run.ts`: `results` keyed by page
id with `status` / `seconds` / `error`, optional `wall_seconds`,
`bytes_fetched`, `pages_per_second`, and — from newer wrappers —
`page_sources` (page id → source image URL, linked from the page id when
http(s)) and `canvas_ids`.

## Layout

| File                                          | What                                                                                                                        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/config.ts`                           | URL and cadence resolution (table above)                                                                                    |
| `src/lib/status.ts`                           | `status.json` schemas and the fail-soft parser                                                                              |
| `src/lib/derive.ts`                           | pure view derivation: `viewerHref`, `campaignHealth`, `isStale`, `pagesLabel`, `shortDate`, `tickSummaryLabel`, `isHttpUrl` |
| `src/lib/run.ts`, `runlog.ts`                 | manifest schema + summary math; run-log grouping                                                                            |
| `src/lib/theme.svelte.ts`                     | the one theme store (`ThemeToggle.svelte` on both routes)                                                                   |
| `src/lib/components/`                         | `CampaignCard`, `RunSummaryCard`, `PageGrid`, `PagesTable`, `ThemeToggle`                                                   |
| `src/routes/+page.svelte`, `log/+page.svelte` | the two routes                                                                                                              |
| `src/app.css`                                 | design tokens per theme (AA-checked), reduced-motion                                                                        |
| `static/config.js`                            | the deployment hook (`window.STATUS_URL`)                                                                                   |
| `static/status.sample.json`                   | the fixture above                                                                                                           |

Tests sit next to their subject (`*.test.ts`); component tests use
@testing-library/svelte + user-event on jsdom, route tests mock `fetch` and
fake timers.
