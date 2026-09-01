# S3 Layout

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<namespace>/<pipeline>/<volume>/` (or
`<pipeline>/<volume>/` with `converter.yaml`'s `legacy_layout: true`) — the
pipeline id is part of the key, so re-running a volume under a new recipe
never overwrites old results. There is no status document written by
anything in this system any more: campaign and volume progress is derived
live from the Kubernetes API by the read API (`packages/api`), never stored.

## Key layout

```
[<namespace>/]<pipeline>/<volume>/
  page/<page>.xml            # per-page PAGE XML, uploaded FIRST (wrapper)
  alto/<page>.xml            # per-page ALTO, uploaded second — "page done" (wrapper)
  iiif.json                  # IIIF v3 viewer manifest with ALTO links (wrapper)
  pipeline.yaml              # the exact steps document the run used (wrapper)
  manifest.json              # completion marker — written LAST (wrapper)

sources/[<namespace>/]<pipeline>/<volume>/
  manifest.json              # synthetic IIIF manifest for IMAGES volumes, published
                             # by the wrapper itself before processing; overwritten
                             # every run

status/
  logs/<pipeline>/<volume>.txt      # the run's own stdout/stderr, shipped live (wrapper)
```

Writers: the **wrapper** is the only writer in the whole tree — its own
`<pipeline>/<volume>/` prefix, its run-log key under `status/logs/`, and
`sources/` (for `IMAGES` volumes). Nothing else in this system writes to S3
at all: the read API is entirely Kubernetes-API-backed and never touches the
bucket. Anonymous read (devStack policy): everything except `status/logs/*`
when `devStack.rustfs.publicLogs=false`. Listing is always denied.

## `manifest.json` (completion marker)

Written only after the verify gate confirms every expected page has **both**
PAGE and ALTO in S3. Its presence *is* "done" for that pipeline id — the
canonical way to check status past a Job's `ttlSecondsAfterFinished` (24 h)
is listing `manifest.json` keys directly, since the read API can only see
Jobs that still exist.

| Field | Meaning |
|---|---|
| `volume`, `pipeline_id` | the key pair |
| `pipeline_sha256` | sha256 of the `pipeline.yaml` text the pod was given — matches the `htrflow.riksarkivet.se/pipeline-sha256` annotation the converter puts on `htr-pipeline-<id>` at render time |
| `pipeline_yaml` | that text |
| `image_digest` | the `IMAGE_DIGEST` env (the pipeline's digest pin); `"unknown"` for results that predate pinning |
| `htrflow_version` | `importlib.metadata.version("htrflow")` in the image |
| `pages` | canvas count |
| `results` | `{"0001": {"status": "ok" \| "failed" \| "skipped", "seconds", "error"?}, …}` |
| `page_sources` | `{"0001": <source image URL, userinfo/query stripped>, …}` — what resume compares |
| `canvas_ids` | `{"0001": <source canvas id or null>, …}` |
| `source_manifest` | the manifest URL the pod fetched (verbatim), or, for `IMAGES` volumes, the synthetic manifest id the wrapper published to `sources/` |
| `max_image_width`, `bytes_fetched`, `wall_seconds`, `gpu_stall_seconds`, `pages_per_second` | run metrics |
| `viewer_url` | the public `iiif.json` URL |

## Live status: the read API, not a file

`GET /api/v1/jobs` and `GET /api/v1/jobs/{namespace}/{name}` (D8) replace
what `status/status.json` used to be — computed on every request from the
live Job/Pod/ConfigMap state, never cached or persisted anywhere:

- **Campaign summary**: `namespace`, `name`, `pipeline`, `phase`
  (`Queued`/`Paused`/`Running`/`Succeeded`/`Failed`, derived from the Job's
  `suspend` flag and its `Complete`/`Failed` conditions), `counts` (`total`
  = `completions`, `active`, `done` = `|completedIndexes|`, `failed` =
  `|failedIndexes|`), `suspended`, `createdAt`, `resultsBase`.
- **Per-volume detail** (paged by index, `offset`/`limit`): one row per line
  of the campaign's `volumes.txt` ConfigMap — `index`, `id`, `state`
  (`done`/`failed`/`active`/`pending`), `manifestUrl`/`iiifUrl`/`altoPrefix`
  (built from `resultsBase`), `logKey`
  (`status/logs/<pipeline>/<id>.txt`, unconditional), and `reason` — the
  failed pod's own termination message — present only while a pod for that
  index still exists.
- **Failures**: up to 50 of the most recent failed-with-a-reason rows,
  included in the detail response.

Full field derivation: [`packages/api/src/htrflow_api/projection.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/api/src/htrflow_api/projection.py).
The frontend's own consumption of this shape is being migrated in B63 Task 7
— see [Campaign Browser](frontend.md).
