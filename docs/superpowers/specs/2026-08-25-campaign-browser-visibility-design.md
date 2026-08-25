# Campaign browser visibility — design

2026-08-25. Requested during the local k3s test drive: (1) the status page
should link to the campaigns git repo, (2) progress should be visible in
pages/images, not only volumes (a 1-volume × 2-image campaign reads "0/1"
today), (3) planned pipelines and volumes should be visible before any job
has run.

## status.json additions (reconciler)

All additions are nullable/optional so old and new producers/consumers
interoperate in either direction.

- **Top level** `campaigns_repo_url: str` — verbatim from
  `CAMPAIGNS_REPO_URL`. The reconciler is the authority on which repo drives
  the cluster; a frontend-configured link could drift from what is actually
  cloned.
- **Per campaign** `pipeline_steps: list[str] | null` — one short line per
  step derived from the parsed pipeline spec:
  `"<Step>: <model> (<model_settings.model>)"`, falling back to `"<Step>"`
  when settings are absent. `null` when the campaign's pipeline is unknown
  or broken.
- **Per volume** `pages_total: int | null` — now emitted:
  - image-list volumes (form 3): `len(volume.images)`;
  - IIIF-manifest volumes: canvas count, captured during the existing
    validation fetch and cached in `validation.json` alongside the
    thumbnail (`page_count` key). No additional network calls.
  - done volumes with no cached count: fall back to the ALTO page count
    (`pages_done`), which for a completed volume equals the processed total.
- **Campaign totals** gain `pages_done: int | null` / `pages_total: int |
  null` — sums over volumes with known values; `null` when no volume has a
  known value. `done`/`total` (volume counts) keep their exact meaning.

`validation.json` schema change is additive (`page_count` next to
`thumbnail`/`format`); existing cache entries simply lack the key and are
treated as unknown (re-fetch is NOT forced — counts appear as volumes are
next validated or when done).

## Frontend (campaign browser SPA)

- **Header**: show `campaigns_repo_url` next to the title — an anchor when
  it starts with `http(s)://`, otherwise plain `<code>` text (git:// URLs
  are not browsable).
- **Campaign row**: `D/T volumes · d/t pages`; the pages segment renders
  only when `pages_total` is non-null. The `<progress>` bar keeps volume
  semantics.
- **Expanded by default**: every campaign section renders its volume grid
  initially; the header click still collapses. `open` state becomes a set
  of collapsed names (inverted from today's single-open-name string).
- **Planned look**: `pending` volume cards get a dashed border and the chip
  reads `planned` (status value in status.json stays `pending`; rename is
  display-only).
- **Pipeline detail**: under the campaign header, a muted one-liner joins
  `pipeline_steps` with " → ". Hidden when null.
- **Schema** (`status.ts`): new fields optional with `.nullable().default(null)`
  / `.default([])`-style fallbacks so an old `status.json` parses unchanged.

## Testing

- Reconciler (pytest): `campaigns_repo_url` passthrough; `pipeline_steps`
  derivation incl. missing-settings fallback; `pages_total` for image-list,
  manifest-with-cached-count, and done-fallback cases; totals aggregation
  incl. all-unknown → null.
- Frontend (`bun run test`): schema accepts old and new documents; derive
  helpers for the pages segment and collapsed-set logic.

## Rollout (local test cluster)

Branch `feat/campaign-browser-visibility`. Images pushed under NEW tags
(`htrflow-reconciler:dev2`, `uv4:dev2`) — same-tag overwrites are invisible
to `IfNotPresent` nodes — and the `htr` release upgraded with `--set
reconciler.image=... --set viewer.image=...`.

## Out of scope

- Aggregate "pages done" for running volumes beyond the existing
  `count_pages` probe (no per-tick deep listing added).
- Any change to job submission, immutability guards, or wrapper.
