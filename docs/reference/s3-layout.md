# S3 Layout

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<namespace>/<pipeline>/<volume>/` — the namespace
comes from `S3_PREFIX`, which the converter always sets to the campaign's
namespace, and the pipeline id is part of the key, so re-running a volume
under a new recipe never overwrites old results. This is the only layout:
the flat `<pipeline>/<volume>/` form results took before B63 is gone, and
Riksarkivet's PoC bucket was moved once (`aws s3 mv`, [Indexed Jobs
E2E](../development/e2e-indexed-jobs.md)). There is no status document
written by anything in this system any more: campaign and volume progress is
derived live from the Kubernetes API by the read API (`packages/api`), never
stored.

## Key layout

```
<namespace>/<pipeline>/<volume>/
  page/<page>.xml            # per-page PAGE XML, uploaded FIRST (wrapper)
  alto/<page>.xml            # per-page ALTO, uploaded second — "page done" (wrapper)
  iiif.json                  # IIIF v3 viewer manifest with ALTO links (wrapper)
  pipeline.yaml              # the exact steps document the run used (wrapper)
  manifest.json              # completion marker — written LAST (wrapper)

<namespace>/sources/<pipeline>/<volume>/
  manifest.json              # synthetic IIIF manifest for IMAGES volumes, published
                             # by the wrapper itself before processing; overwritten
                             # every run

status/
  logs/<pipeline>/<volume>.txt      # the run's own stdout/stderr, shipped live (wrapper)
```

Writers: the **wrapper** is the only writer in the whole tree — its own
`<namespace>/<pipeline>/<volume>/` prefix, its run-log key under
`status/logs/`, and `sources/` (for `IMAGES` volumes). `S3_PREFIX` goes in
*front* of the `sources/` key, so the synthetic manifests sit at
`<namespace>/sources/…`, not `sources/<namespace>/…`; `status/` alone is
namespace-free, since the browser resolves run-log links against the bucket
root. Nothing else in this system writes to S3 at all: the read API is
entirely Kubernetes-API-backed and never touches the bucket. Anonymous read (devStack policy): everything except `status/logs/*`
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

`GET /api/v1/jobs` and `GET /api/v1/jobs/{namespace}/{name}` (D8) are the
whole story: no status document is written anywhere any more (see the note
at the top of this page) — every response is computed live from the
Job/Pod/ConfigMap state, never cached or persisted:

- **Campaign summary**: `namespace`, `name`, `pipeline`, `phase`
  (`Queued`/`Paused`/`Running`/`Succeeded`/`Failed`, derived from the Job's
  `suspend` flag and its `Complete`/`Failed` conditions), `counts` (`total`
  = `completions`, `active`, `done` = `|completedIndexes|`, `failed` =
  `|failedIndexes|`), `suspended`, `createdAt`, `resultsBase`.
- **Per-volume detail** (paged by index, `offset`/`limit`): one row per line
  of the campaign's `volumes.txt` ConfigMap — `index`, `id`, `state`
  (`done`/`failed`/`active`/`pending`), `manifestUrl`/`iiifUrl`/`altoPrefix`
  (built from `resultsBase`), `logUrl` — an absolute URL
  (`<public_results_base>/status/logs/<pipeline>/<id>.txt`, unconditional;
  bucket-root, no namespace/S3_PREFIX prefix, since the browser has no
  bucket base URL to resolve a bare key against) — and `reason` — the failed
  pod's own termination message — present only while a pod for that index
  still exists.
- **Failures**: up to 50 of the most recent failed-with-a-reason rows,
  included in the detail response.

Full field derivation: [`packages/api/src/htrflow_api/projection.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/api/src/htrflow_api/projection.py).
The frontend consumes this shape directly — see [Campaign Browser](frontend.md).
