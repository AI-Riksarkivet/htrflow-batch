# Campaign Browser

The SvelteKit SPA served at `/` by the viewer image — one page that renders
`status.json` into campaign cards with per-volume status, thumbnails, and
links into Universal Viewer. Source:
[`frontend/`](https://github.com/carpelan/test/tree/main/frontend)

## Stack

Svelte 5 (runes) + SvelteKit 2 with `adapter-static` (`prerender = true`,
`ssr = false` — a pure static shell, data fetched in the browser), strict
TypeScript, Zod at the boundary, Vitest, Bun as the package runner.

| File | Description |
|------|-------------|
| `src/lib/status.ts` | Zod schemas mirroring the reconciler's `status.json` ([schema](s3-layout.md#statusjson)) — parse, don't validate |
| `src/lib/derive.ts` | Pure view derivation: `viewerHref`, `progress`, `isStale` |
| `src/routes/+page.svelte` | The page: fetch, parse, render; error and stale banners |
| `static/status.sample.json` | Dev fixture |

## Configuration

The status URL is resolved at click-time, not build-time:

```ts
const statusUrl = (): string => window.STATUS_URL ?? DEFAULT_STATUS_URL;
```

Set `window.STATUS_URL` (e.g. from an inline script the deployment injects)
to point elsewhere; the default is the PoC RustFS NodePort
(`http://localhost:30900/htr-results/status/status.json`).

## Derivation rules

- **Viewer link** — a `done` volume links to its published `viewer_manifest`
  (results + ALTO overlays); any other state links to its `source_manifest`
  (the raw scans), both as `uv.html#?manifest=<url>`.
- **Progress** — `round(100 * done / total)`, `0` for an empty campaign.
- **Stale banner** — shown when `generated_at` is older than
  `3 × tick_seconds` (15 min at the default tick): the reconciler is presumed
  dead, the numbers are historical.

## Commands

```bash
cd frontend
bun install
bun run dev      # dev server against static/status.sample.json
bun run check    # svelte-check, strict TS
bun run test     # vitest
bun run build    # static build → consumed by .docker/uv4-viewer.dockerfile
```

The viewer image (`.docker/uv4-viewer.dockerfile`,
`nginxinc/nginx-unprivileged`, port 8080) serves this build at `/` and
Universal Viewer at `/uv.html`.
