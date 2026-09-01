# S3 Layout & status.json

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<pipeline>/<volume>/` — the pipeline id is part of the
key, so re-running a volume under a new recipe never overwrites old results.

## Key layout

```
<pipeline>/<volume>/
  page/<page>.xml            # per-page PAGE XML, uploaded FIRST (wrapper)
  alto/<page>.xml            # per-page ALTO, uploaded second — "page done" (wrapper)
  iiif.json                  # IIIF v3 viewer manifest with ALTO links (wrapper)
  pipeline.yaml              # the exact steps document the run used (wrapper)
  manifest.json              # completion marker — written LAST (wrapper)

sources/<pipeline>/<volume>/
  manifest.json              # synthetic IIIF manifest for IMAGES volumes, published
                             # by the wrapper itself before processing; overwritten
                             # every run (no content hash in the key)

status/
  status.json                # the campaign browser's data source (reconciler)
  attempts.json              # retry budgets + sticky verdicts, "<pipeline>/<volume>" and "warmup/<pipeline>" (reconciler)
  validation.json            # cached manifest verdicts, thumbnails, page counts (reconciler)
  volumes.json               # per-volume probe cache keyed by manifest mtime — safe to delete (reconciler)
  logs/<pipeline>/<volume>.txt      # the run's own stdout/stderr, shipped live (wrapper; kube-tail fallback by the reconciler)
  failures/<pipeline>/<volume>.txt  # the failed attempt's log, copied before the Job is deleted (reconciler)
  warmup/<pipeline>.log             # log of a failed warm-up Job (reconciler)
```

Writers: the **wrapper** writes under its own `<pipeline>/<volume>/` prefix,
its run-log key, and `sources/` (for `IMAGES` volumes); the **reconciler**
owns the rest of `status/` and never writes into result prefixes. Anonymous read (devStack
policy): everything except `status/attempts.json`, `validation.json`,
`failures/*`, `warmup/*` — and `status/logs/*` when
`devStack.rustfs.publicLogs=false`. Listing is always denied.

## `manifest.json` (completion marker)

Written only after the verify gate confirms every expected page has **both**
PAGE and ALTO in S3. Its presence *is* "done" — the reconciler probes it
with HEAD requests (`done_volumes`) and uses its `LastModified` as the
volume's `updated` stamp.

| Field | Meaning |
|---|---|
| `volume`, `pipeline_id` | the key pair |
| `pipeline_sha256` | sha256 of the `pipeline.yaml` text the Job was given — the drift ground truth (the reconciler accepts it or the canonical-JSON hash of the same steps) |
| `pipeline_yaml` | that text |
| `image_digest` | the `IMAGE_DIGEST` env (the pipeline's digest pin); `"unknown"` for results that predate pinning |
| `htrflow_version` | `importlib.metadata.version("htrflow")` in the image |
| `pages` | canvas count |
| `results` | `{"0001": {"status": "ok" \| "failed" \| "skipped", "seconds", "error"?}, …}` |
| `page_sources` | `{"0001": <source image URL, userinfo/query stripped>, …}` — what resume compares |
| `canvas_ids` | `{"0001": <source canvas id or null>, …}` |
| `source_manifest` | the manifest URL the Job fetched (verbatim) |
| `max_image_width`, `bytes_fetched`, `wall_seconds`, `gpu_stall_seconds`, `pages_per_second` | run metrics |
| `viewer_url` | the public `iiif.json` URL |

## `status.json`

Rewritten every tick; the campaign browser parses it fail-soft
([`frontend/src/lib/status.ts`](https://github.com/carpelan/test/blob/main/frontend/src/lib/status.ts)).
`frontend/static/status.sample.json` is a complete, test-guarded example.

```json
{
  "generated_at": "2026-08-26T08:55:12Z",
  "tick_seconds": 300,
  "campaigns_repo_url": "https://github.com/<org>/htr-campaigns",
  "warnings": [
    "pipeline demo-v1: warming model cache (htr-warmup-demo-v1)",
    "image allow-list empty (RECONCILER_ALLOWED_IMAGE_REPOS): any digest-pinned image in the campaigns repo will run on the GPU"
  ],
  "tick_summary": { "seconds": 4.06, "s3_calls": 12, "validations": 3, "submitted": 1, "retried": 0 },
  "campaigns": [
    {
      "name": "swedish-spreads",
      "pipeline": "demo-v1",
      "pipeline_steps": ["Segmentation: yolo (Riksarkivet/yolov9-regions-1)", "TextRecognition: TrOCR (…)"],
      "pipeline_yaml": "steps:\n- step: Segmentation\n  …",
      "error": null,
      "totals": { "done": 1, "total": 2, "pages_done": 722, "pages_total": 1118 },
      "orphans": ["volume-in-s3-but-not-in-git"],
      "volumes": [
        {
          "id": "R0001203",
          "status": "done",
          "attempts": 0,
          "terminal": null,
          "pages_done": 638,
          "pages_total": 638,
          "error": null,
          "updated": "2026-08-25T13:29:10Z",
          "failure_log": null,
          "run_log": "http://…/htr-results/status/logs/demo-v1/R0001203.txt",
          "run_manifest": "http://…/htr-results/demo-v1/R0001203/manifest.json",
          "viewer_manifest": "http://…/htr-results/demo-v1/R0001203/iiif.json",
          "source_manifest": "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest",
          "thumbnail": "https://lbiiif.riksarkivet.se/arkis!R0001203_00001/full/200,/0/default.jpg"
        },
        {
          "id": "R0001696",
          "status": "needs-attention",
          "attempts": 3,
          "terminal": "capped",
          "pages_done": null,
          "pages_total": 480,
          "error": null,
          "updated": null,
          "failure_log": "http://…/htr-results/status/failures/demo-v1/R0001696.txt",
          "run_log": null,
          "run_manifest": null,
          "viewer_manifest": null,
          "source_manifest": "https://lbiiif.riksarkivet.se/arkis!R0001696/manifest",
          "thumbnail": null
        }
      ]
    }
  ]
}
```

Notes:

- `status` is one of `done | running | queued | retry | deleting |
  needs-attention | pending | unreachable | unsupported` — see the
  [state table](reconciler.md#volume-states). The browser renders anything
  else as `unknown`.
- `terminal` is `"exit-13"`, `"capped"` or `null`: the sticky
  `needs-attention` verdict from `attempts.json`. Clearing it is an operator
  action.
- `viewer_manifest` is non-null only when done; the browser links it into UV.
- `run_log` (`status/logs/<pipeline>/<volume>.txt`) is the wrapper's own
  stdout/stderr, shipped every 15 s while the volume runs and complete after
  it — set for `done`/`running`/`queued` whenever the object exists, so the
  frontend can follow a run live. `run_manifest` is the run's
  `manifest.json` for the same statuses (404 until published).
  `failure_log` (`status/failures/…`) carries the failed attempt's evidence
  for `retry`/`needs-attention`: the complete shipped log when there is one,
  else a kube-API tail. On retry the run-log key is retired so the next
  attempt is never shown the previous one's log.
- `updated` is the `manifest.json` LastModified of a done volume, else null.
- `pages_done` is counted (ALTO objects) for `done` (cached per mtime) and
  `running` volumes; `pages_total` is the manifest's canvas count (cached by
  pre-validation) or the length of `images:` for synthetic volumes, else
  `pages_done` once done.
- `thumbnail` is a **sized** IIIF request (`/full/200,/0/default.jpg`) when
  the first canvas carries an image service, else `null` — never the
  full-size scan. Synthetic (`images:`) volumes are always `null`.
- `error` is set when one of that volume's S3/kube effects failed during the
  tick; the rest of the row stands.
- **URL rewriting:** every URL in `status.json` is browser-facing. Anything
  hosted on the in-cluster S3 endpoint (`S3_ENDPOINT`, any bucket) is
  rewritten to its twin under `PUBLIC_RESULTS_BASE`'s endpoint; other hosts
  pass through untouched. The Job, by contrast, is given the in-cluster URL
  of a synthetic manifest (`internal_results_base`), because
  `PUBLIC_RESULTS_BASE` may be `localhost` through an SSH forward.
- `orphans` lists volume ids that have results under the pipeline prefix but
  appear in no campaign — reported once per pipeline, on the first campaign
  using it. A campaign that fails to parse still claims the ids it names.
- Campaign-level `error` carries parse failures, unknown pipeline ids and a
  failed done-set LIST; the volume list is empty in that case.
- `campaigns_repo_url` is `reconciler.campaignsRepoWebUrl`, falling back to
  the clone URL; `tick_summary` is what the last full tick cost (`s3_calls`
  includes the `status.json` write).

## `attempts.json`, `validation.json`, `volumes.json`

`attempts.json` (v2):

```json
{
  "demo-v1/R0001696": { "n": 3, "terminal": "capped" },
  "demo-v1/R0001203": { "n": 1, "terminal": null, "pages_at_submit": 120 },
  "warmup/demo-v2":   { "n": 1, "terminal": "exit-13" }
}
```

`n` counts charged attempts; `terminal` is the sticky verdict
(`"exit-13"` / `"capped"` / `null`); `pages_at_submit` is `pages_done` when
the current Job was created, so a deadline/SIGTERM kill that advanced it is
not charged. Keyed per pipeline so a volume that burned its budget on
`demo-v1` starts fresh on `demo-v2`; warm-ups share the file under
`warmup/<pipeline>`. v1 files (bare ints) are migrated on read; a corrupt
record loses only itself.

`validation.json` maps `manifest URL → verdict`:

```json
{
  "https://…/manifest": { "format": "p3", "thumbnail": "https://…/full/200,/0/default.jpg", "page_count": 638 },
  "https://…/coll":     { "format": "unsupported", "thumbnail": null, "page_count": null },
  "https://…/gone":     { "format": "unreachable", "thumbnail": null, "page_count": null, "permanent": true },
  "https://…/flaky":    { "format": "unreachable", "thumbnail": null, "page_count": null, "unreachable_until": "2026-08-26T09:10:12Z" }
}
```

Document verdicts (`p2`/`p3`/`unsupported`) and permanent 4xx
(`unreachable` + `permanent`) are cached forever; a transient `unreachable`
is re-probed once `unreachable_until` (3 ticks) has passed.

`volumes.json` maps `"<pipeline>/<volume>"` to a probe cache —
`{"updated": <manifest mtime>, "pages": <alto count>, "run_log": <bool>,
"synthetic": <sources key>}` — invalidated when the mtime changes. It is
safe to delete; the next tick rebuilds it.
