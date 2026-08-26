# S3 Layout & status.json

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<pipeline>/<volume>/` — the pipeline id is part of the
key, so re-running a volume under a new recipe never overwrites old results.

## Key layout

```
<pipeline>/<volume>/
  alto/<page>.xml            # per-page ALTO, streamed as pages finish (wrapper)
  page/<page>.xml            # per-page PAGE XML (wrapper)
  iiif.json                  # IIIF v3 viewer manifest with ALTO links (wrapper)
  pipeline.yaml              # the exact pipeline YAML the run used (wrapper)
  manifest.json              # completion marker — written LAST (wrapper)
  metrics-failed-latest.json # evidence from the most recent FAILED run (wrapper)

sources/<pipeline>/<volume>/
  manifest.json              # synthetic IIIF manifest for images: volumes (reconciler)

status/
  status.json                # the campaign browser's data source (reconciler)
  attempts.json              # retry budgets, keyed "<pipeline>/<volume>" (reconciler)
  validation.json            # cached manifest verdicts + thumbnails (reconciler)
  failures/<pipeline>/<volume>.txt   # captured pod logs from each failed Job (reconciler)
```

Writers: the **wrapper** only writes under its own `<pipeline>/<volume>/`
prefix; the **reconciler** owns `sources/` and `status/` and never writes into
result prefixes.

## `manifest.json` (completion marker)

Written only after the verify gate confirms every expected page has ALTO in
S3. Its presence *is* "done" — the reconciler probes it with HEAD requests
(`done_volumes`). Fields: `volume`, `pipeline_id`, `pipeline_sha256` (drift
ground truth), `pipeline_yaml`, `htrflow_version`, `image_digest`, `pages`,
per-page `results` (status/seconds/error), `source_manifest`,
`max_image_width`, `bytes_fetched`, `wall_seconds`, `gpu_stall_seconds`,
`pages_per_second`.

## `status.json`

Rewritten every tick; the campaign browser Zod-parses it
([`frontend/src/lib/status.ts`](https://github.com/carpelan/test/blob/main/frontend/src/lib/status.ts)).

```json
{
  "generated_at": "2026-08-24T12:00:00Z",
  "tick_seconds": 300,
  "warnings": ["pipeline demo-v1: …"],
  "campaigns": [
    {
      "name": "htr-demo-examples",
      "pipeline": "demo-v1",
      "error": null,
      "totals": { "done": 1, "total": 2 },
      "orphans": ["volume-in-s3-but-not-in-git"],
      "volumes": [
        {
          "id": "R0001203",
          "status": "done",
          "attempts": 0,
          "pages_done": 24,
          "pages_total": null,
          "error": null,
          "updated": "2026-08-25T13:29:10Z",
          "failure_log": null,
          "run_log": "…/status/logs/demo-v1/R0001203.txt",
          "run_manifest": "…/demo-v1/R0001203/manifest.json",
          "viewer_manifest": "…/demo-v1/R0001203/iiif.json",
          "source_manifest": "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest",
          "thumbnail": "…/full/200,/0/default.jpg"
        }
      ]
    }
  ]
}
```

Notes:

- `status` is one of `done | running | queued | retry | needs-attention |
  pending | unreachable | unsupported` — see the
  [state table](reconciler.md#volume-states).
- `viewer_manifest` is non-null only when done; the browser links it into UV.
- `run_log` (`status/logs/<pipeline>/<volume>.txt`) is the wrapper's own
  stdout/stderr, shipped every 15 s while the volume runs and complete after
  it — set for `done`/`running`/`queued` whenever the object exists, so the
  frontend can follow a run live. `run_manifest` is the run's `manifest.json`
  for the same statuses (404 until published). `failure_log`
  (`status/failures/…`) carries the failed attempt's evidence for
  `retry`/`needs-attention`: the shipped log when there is one, else a
  kube-API tail. On retry the run-log key is retired so the next attempt is
  never shown the previous one's log.
- `updated` is the `manifest.json` LastModified of a done volume, else null.
- `pages_done` is counted (ALTO objects) only for `done`/`running` volumes;
  `pages_total` is the manifest's canvas count (cached by pre-validation) or
  the length of `images:` for synthetic volumes.
- Every URL in status.json is browser-facing: anything hosted on the
  in-cluster S3 endpoint is rewritten to the public endpoint (any bucket).
- `orphans` lists volume ids that have results under the pipeline prefix but
  appear in no campaign — reported once per pipeline, on the first campaign
  using it.
- Campaign-level `error` carries parse failures and unknown pipeline ids; the
  volume list is empty in that case.

## `attempts.json` and `validation.json`

`attempts.json` maps `"<pipeline>/<volume>" → count`. Keyed per pipeline so a
volume that burned its budget on `demo-v1` starts fresh on `demo-v2`.

`validation.json` maps `manifest URL → {format, thumbnail}`. Document verdicts
(`p2`/`p3`/`unsupported`) are cached forever; `unreachable` is never cached —
a flaky fetch must not permanently wedge a volume.
