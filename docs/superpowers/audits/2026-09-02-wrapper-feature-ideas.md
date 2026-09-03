# High-leverage features around the wrapper (2026-09-02)

Ideas, not a plan. Companion to `2026-09-02-wrapper-audit.md`,
`2026-09-02-wrapper-simplifications.md` and
`2026-09-02-wrapper-to-kubernetes.md`. Against HEAD `9e074e0` on
`b63-indexed`. Each item names the data or hook that already exists, so the
feature is mostly wiring. Ranked by value per line of code. Before building
any of them: brainstorm → spec → plan (the superpowers flow), one worktree
per feature, tiny commits, `(B63)` or the feature's own story id in the
subject, no Co-Authored-By trailer, `uv` never pip. The converter has a
hard LOC budget; check it before adding there.

---

## 1. Pre-flight manifests in the converter before creating the Job

**Problem.** One bad manifest URL in a thousand-volume campaign exits 13 →
`FailIndex` → with `maxFailedIndexes: 1` the whole campaign fails, after a
GPU pod was scheduled and a model loaded.

**Exists already.** The converter holds every `volumes.txt` line. The
wrapper's `iiif.fetch_manifest` and `iiif.pages_from_manifest` are pure
functions with the S5 byte cap and the permanent/transient split; the
wrapper package imports without torch.

**Build.** A `preflight` step in `htrflow-campaigns apply` (and a
standalone command) that fetches each manifest on the CPU host, runs
`pages_from_manifest`, and reports per volume: page count, or the exact
`ManifestError`. Refuse to apply on any permanent error unless
`--no-preflight`. `IMAGES` volumes: run `check_http_url` on each URL only.
Honour `MANIFEST_MAX_BYTES` and `MAX_IMAGE_WIDTH` from the same config the
Job gets. Side benefit: the total page count per campaign, which item 5
turns into an estimate.

**Size.** ~100 lines converter + tests with an httpx mock transport.
**Watch.** Converter LOC budget; the work network blocks some IIIF hosts
(FortiGate) — preflight must run where the cluster's egress runs, or be
skippable per host.

## 2. Plain-text export beside ALTO and PAGE

**Problem.** Downstream users (search indexing, the Lance/MCP work,
researchers) want text, not XML.

**Exists already.** `store.upload_page` reads and parses both XML bodies
before the first PUT (W3). `viewer.parse_alto_dims_bytes` already walks the
ALTO tree.

**Build.** From the parsed ALTO, emit `text/<page>.txt` (one line per
`TextLine`, reading order) and append to a per-volume `volume.jsonl` with
`{page, line_id, text, hpos, vpos, width, height, confidence}`. Upload
`text/` per page with the other two formats (before ALTO, so ALTO stays the
last write); write `volume.jsonl` in `publish.run` from the local ALTO
files (and stored ALTO for resumed pages, as `alto_dims` does). Add both to
`docs/reference/s3-layout.md`. Do not add `text` to `PAGE_FORMATS`
(that changes `done_pages` and the resume contract); treat it as a derived
output that a resumed volume can regenerate.

**Size.** ~80 lines wrapper + tests on the fixture ALTO. The wrapper LOC
budget is 2000; this fits only if the simplifications land first.

## 3. Per-volume progress heartbeat

**Problem.** The campaign card shows "running" until the run log's terminal
line; no ETA per volume or per campaign.

**Exists already.** `LogCapture` wakes every `LOG_SHIP_SECONDS` on its own
thread with `ResultStore._log_client` (bounded timeouts). `StreamStats`
holds per-page outcomes and `stall_seconds`; `PageStream.bytes_fetched`
and the page total are known from `_setup`.

**Build.** A `status/progress/<pipeline>/<volume>.json` written on the same
tick as the log: `{pages, done, failed, pages_per_second, eta_seconds,
stage, updated}`. Same key discipline as `run_log_key` (bucket root, not
`volume_prefix`); same best-effort rule (never fails the run). The read API
projects it beside `logUrl`; `CampaignCard.svelte` renders a bar. Delete the
object on publish or leave it as the final state — decide in the spec.

**Size.** ~20 lines wrapper, ~15 API, one component. **Watch.** Public
bucket: no URLs in the JSON.

## 4. Per-page confidence in the completion marker

**Problem.** No way to find the worst pages of a campaign or reprocess only
pages under a threshold.

**Exists already.** The XML is parsed at upload. htrflow writes recognition
confidence to its output — verify in the installed htrflow's templates
whether it lands in the PAGE `TextEquiv/@conf` or the ALTO `String/@WC`
(or both) before choosing which file to read.

**Build.** Mean and min line confidence per page, added to
`results[name]` in `manifest.json` (`publish._results_json`) via a new
field on `PageOutcome`. Then a converter/API query "pages under X" and,
later, a `REPROCESS_BELOW` resume rule (a page is not done if its recorded
confidence is under the threshold — extends `_changed_sources`).

**Size.** ~30 lines wrapper for the field; the resume rule is a separate
spec. Pairs with item 2 (the JSONL carries the same number per line).

## 5. Campaign rollup from the completion markers

**Problem.** GPU-hours per 100 000 pages is the number that sizes the queue,
and it is currently computed by hand.

**Exists already.** Every `manifest.json` carries `pages`, `wall_seconds`,
`gpu_stall_seconds`, `pages_per_second`, `bytes_fetched`, `htrflow_version`,
`image_digest` and the per-page results.

**Build.** A read-API endpoint per campaign (and per pipeline) that lists
the volume prefixes, reads each marker, and returns sums and rates:
pages, wall hours, stall share, pages/s (median and p10), bytes, failed
pages, and the spread of `image_digest`/`htrflow_version` (drift check).
Cache by marker ETag. No wrapper change. With item 1's page counts this
also yields an estimate for a campaign before it runs.

**Size.** ~80 lines API + a frontend table.

## 6. Search inside the volume in the viewer

**Problem.** Archivists' first question is "find this word in this
volume"; the ALTO is there but not searchable from the viewer.

**Exists already.** `viewer.build_viewer_manifest` declares a
`SearchService1` at `{vol}/search` so UV4 (RA fork) shows the ALTO text
panel; the endpoint is a stub. ALTO carries line and string coordinates.

**Build.** IIIF Content Search 1.0 in the read API: `GET
/<pipeline>/<volume>/search?q=` → an AnnotationList of hits targeting
`{vol}/canvas/<page>#xywh=…`. Index source: item 2's `volume.jsonl` if it
exists, else parse the stored ALTO on demand and cache. Point the manifest's
service `@id` at the API host (today it points into the results bucket,
which cannot answer a query).

**Size.** ~150 lines API + tests; moderate. The one users notice first.

---

## Not now

- Auto-tuning `DOWNLOAD_CONCURRENCY`/`LOOKAHEAD_PAGES` from
  `gpu_stall_seconds`: the knob exists, the data exists, but one static
  setting per pipeline has been fine; revisit if item 5 shows stall > 10 %.
- A separate campaign estimate command: falls out of item 1 (page counts)
  plus item 5 (rates).
- Reprocess-failed-only command: `apply` + resume already do this on retry;
  item 4's threshold rule is the version worth having.

## Suggested order

1 → 3 → 2 → 4 → 5 → 6. Items 1 and 3 are independent of each other and of
the wrapper simplifications; item 2 wants the simplifications first for
LOC-budget room; 6 wants 2.
