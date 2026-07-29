# Campaign browser

SvelteKit 2 + Svelte 5 SPA over the reconciler's `status.json`. `bun run build`
emits static files to `dist/`; `bun run dev` serves them with hot reload.

The SPA reads `window.STATUS_URL`, falling back to
`http://localhost:30900/htr-results/status/status.json`.

## Dev fixture

`bun run dev` serves the bundled sample at `/status.sample.json`.
In the browser console set `window.STATUS_URL = "/status.sample.json"`.
The next auto-refresh (within 60 s) picks it up — do not reload, that clears it.
