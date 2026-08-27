# Handoff — A4 frontend

Branch `worktree-agent-a2ff4daf17dab8885`, 12 commits on `652a5eb`, `frontend/**`
only. Gate: `bun run test` (76 tests, 10 files; was 26 / 3), `bun run check`,
`bun run lint`, `bun run build` — all green. Coverage 92 % lines.

## What the reconciler / wrapper contract looks like from here

Everything A1 announced is parsed and rendered; nothing is *required* of the
reconciler beyond what it already emits. For the record, what the browser
relies on:

| Field | Browser behaviour |
| --- | --- |
| volume `terminal` (`"exit-13"` \| `"capped"` \| `null`) | tag next to the status chip; title says an operator clears it (attempts record / pipeline id). Absent → `null`. Any other string renders verbatim with a generic hint. |
| top-level `tick_summary` `{seconds, s3_calls, validations, submitted, retried}` | one line in the header meta; every field optional, `{}` / junk / absent → no line. |
| volume `status` `deleting` | a known status now (styled with queued/retry, counts as *active* for the card accent). Anything newer still falls to `unknown` + neutral chip. |
| volume `thumbnail` `null` | neutral placeholder square; a broken image becomes the same square. |
| volume `error` (string) | rendered under the id, wraps. |
| URL fields | only absolute `http(s)`; anything else → `null` + a `status.json: …` warning row. `source_manifest` shape for `images:` volumes (`sources/<pid>/<vid>/<hash8>/manifest.json`) is just another URL. |
| manifest.json `page_sources` / `canvas_ids` | page ids link to the source image when it is `http(s)`; `canvas_ids` is parsed and unused. |

Two things the reconciler could add that would show up without frontend work:

- `tick_summary` is `{}` in the doc skeleton and only filled at the end of
  `run()`; if a tick dies mid-way and the previous status.json is what
  stands, the line reflects the *previous* tick, which is right. Nothing to do.
- For `unreachable` volumes the browser shows `error` as-is. If the
  reconciler put the back-off ("retry in 3 ticks") into that string, the
  operator would see it; the sample assumes it does.

The live cluster's status.json still predates the A1 merge (no
`tick_summary`, no `terminal`); the header line and the tags appear once
the new reconciler image is rolled.

## Docs to update (B2 scope — `docs/reference/frontend.md` and friends)

`docs/reference/frontend.md` is stale on almost every line; the frontend
README (`frontend/README.md`) is now the source and can be lifted almost
verbatim:

- **File table**: add `src/lib/config.ts`, `run.ts`, `runlog.ts`,
  `theme.svelte.ts`, `components/{CampaignCard,RunSummaryCard,PageGrid,PagesTable,ThemeToggle}.svelte`,
  `routes/log/+page.svelte`, `static/config.js`.
- **Configuration**: the `window.STATUS_URL ?? DEFAULT` snippet is gone.
  Resolution is `window.STATUS_URL` (set by **overwriting `/config.js`**,
  same-origin) → `VITE_STATUS_URL` (build) → default. "From an inline script
  the deployment injects" is now *wrong*: the page ships a hash-mode CSP
  (`script-src 'self'` + SvelteKit's init hash, `object-src 'none'`,
  `base-uri 'self'`) and an injected inline script is blocked. Add
  `VITE_RELOAD_MS` / `VITE_LIVE_MS` and `LIVE_MAX_FAILURES = 20`.
- **Derivation rules**: add `deleting` to the blue set; `unknown` to grey;
  the `terminal` tag; the tick-summary header line; fail-soft (envelope
  strict, campaign/volume degrade to error rows with a warning line; last
  good document stays through a failed poll under an alert banner); URL
  fields http(s)-only.
- **Run viewer** (`/log`): summary strip (ok/failed/skipped, total + wall,
  median/p95/max, five slowest), one cell per page (colour = status, height
  = seconds, roving tabindex), failed pages with errors, full table behind
  `<details>` in slices of 100; live mode stops on the terminal line, a
  manifest that covers every page, or 20 failed polls.
- **Commands**: add `coverage`, `lint`, `format`; `engines` Node ≥ 22, Bun ≥ 1.1.
- `docs/reference/s3-layout.md#statusjson`: the example needs
  `campaigns_repo_url`, `tick_summary`, `pipeline_steps`/`pipeline_yaml`,
  `totals.pages_*`, and per volume `run_manifest`, `updated`,
  `failure_log`, `run_log`, `terminal` — `frontend/static/status.sample.json`
  is a complete, test-guarded example (a test asserts it parses with zero
  problems and carries every key).
- `docs/how-it-works/campaigns.md`: mention the `terminal` tag as the visual
  of the sticky needs-attention verdict, and `deleting` as the status a
  retry passes through.

## Outside `frontend/**` (not done here)

- **CI (X11 / F15)**: `.dagger/checks.go` still runs no frontend step. Add a
  `CheckFrontend` that runs `bun install --frozen-lockfile && bun run lint
  && bun run check && bun run test && bun run build` in an `oven/bun`
  container (CA bundle wiring as for uv).
- **Chart (A3)**: `charts/htrflow-batch/templates/viewer.yaml` sets no
  `STATUS_URL` — the deployed viewer runs on the build-time default
  (`http://localhost:30900/…`). The right hook is a ConfigMap-mounted
  `/usr/share/nginx/html/config.js` containing
  `window.STATUS_URL = "<resultsPublicBase>/status/status.json";` (the
  nginx conf already has `try_files /log.html` for the run viewer). If the
  chart ever adds a `Content-Security-Policy` header it must not be stricter
  than the meta tag (include the same script hash) — or send none.
- `.docker/uv4-viewer.dockerfile` copies `dist/` as-is; `config.js` ships
  with the comment-only default, so the ConfigMap mount above overrides it
  cleanly.

## Screenshots (local `vite preview` of `dist/`, `window.STATUS_URL` set via `/config.js` to the live read-only status.json)

`/tmp/claude-1001/-home-morgan/5d8b13b0-3e87-4393-9540-a91074aa8f3f/scratchpad/`:
`fix-home-{390,768,1500}-{light,dark}.png`, `fix-log-1500-{light,dark}.png`,
`fix-log-390-light.png` (full page) and `fix-log-{1500,390}-light-top.png`
(viewport). Zero console errors or warnings, zero CSP reports, zero failed
requests, no horizontal overflow at any width (checked by the script).

## Commits

```
55df5d4 frontend: README and fixture on the full status.json shape (F16)
5e0723f frontend: formatting pass, keyboard and volume-scale component tests (F15)
500dfcc frontend: A1 status.json additions and one config module (R1, X1, W-page_sources)
0eb8753 frontend: AA tokens, shared theme, live-poll cap, one clock (F5, F8–F14)
3d0fe64 frontend: campaign header as sibling buttons (F4, X18)
2811e43 frontend: small-screen tables and log lines (F3, F7, X18)
5672415 frontend: low-priority thumbnails and a neutral placeholder (F2, X10)
0bedb8d frontend: http(s)-only URLs and a hash CSP (F6, S4, X13)
a682e79 frontend: fail-soft status boundary (F1, X9)
c1e72a6 frontend: run viewer that reads at 480 pages
f346811 frontend: component-test and format tooling (F15 groundwork)
```
