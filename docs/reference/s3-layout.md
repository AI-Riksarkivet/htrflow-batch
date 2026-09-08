# S3 Layout

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<namespace>/<pipeline>/<volume>/` — the namespace
comes from `S3_PREFIX`, which the converter always sets to the campaign's
namespace, and the pipeline id is part of the key, so re-running a volume
under a new recipe never overwrites old results. This is the only layout:
the flat `<pipeline>/<volume>/` form results took before B63 is gone, and
Riksarkivet's PoC bucket was moved once (`aws s3 mv`, [Indexed Jobs
E2E](../development/e2e-indexed-jobs.md)). Campaign state is derived live from
the Kubernetes API by the read API (`packages/web`); the one thing the
cluster cannot answer — how many pages a running volume has done — the
wrapper writes beside its results as `progress.json` (below).

## Key layout

```
<namespace>/<pipeline>/<volume>/
  page/<page>.xml            # per-page PAGE XML, uploaded FIRST (wrapper)
  alto/<page>.xml            # per-page ALTO, uploaded second — "page done" (wrapper)
  iiif.json                  # IIIF v3 viewer manifest with ALTO links (wrapper);
                             # rewritten every 10 pages WHILE the run goes, so the
                             # volume opens in the viewer before it is finished
  progress.json              # how far this volume has got — rewritten after every
                             # page and at every stage change (wrapper)
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
root. Nothing else in this system writes to S3 at all — the read API only
*reads*, and only `progress.json`/`manifest.json`, for the volume rows it is
about to answer with (§ Live status). The API pod does this itself, not the
browser: it needs its own path to the bucket (`HTRFLOW_INTERNAL_RESULTS_BASE`
— docs: [Chart Values](chart.md#web-front-web), [Local k3s
development](../development/local-k3s.md)), separate from
`HTRFLOW_PUBLIC_RESULTS_BASE`, the address it hands the browser; on a
deployment where those differ (the PoC), the two must not be confused, or
the API pod silently reads no progress at all. Anonymous read
(devstack policy): everything except `status/logs/*` when the devstack
chart's `rustfs.publicLogs` is `false`. Listing is always denied.

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

## `progress.json` (live, and never a completion marker)

A few hundred bytes, overwritten by the wrapper after every page outcome and
at every stage change — the only way "137 of 638 pages" leaves the pod while
the pod is still running. It is best-effort in both directions: a write that
fails is logged and forgotten (a status file must never cost a page its
work), and a reader that cannot fetch it shows no progress rather than an
error. It says nothing about completion — `manifest.json` alone still does
that, and is still written last.

| Field | Meaning |
|---|---|
| `stage` | `setup`, `resume`, `load`, `stream`, `verify`, `publish`, `done` — the wrapper's own stage names |
| `pages_total` | canvases in the manifest this run covers |
| `pages_done` | pages in the bucket: this run's `ok` pages **plus** the ones a previous run finished and resume skipped |
| `pages_failed` | pages this run recorded as failed |
| `last_page` | the page whose outcome was recorded last |
| `last_error` | `{"page", "error"}` for the most recent failed page — the wrapper's own sentence, URL-redacted (S6) and capped at 300 characters — or `null` |
| `errors` | ERROR-and-worse log records so far, counted as they are emitted. Not WARNING: the wrapper logs its own benign warnings (a pipeline rebuild after a dead worker thread, "viewer manifest covers n/m pages") that must not light a "something went wrong" chip on a healthy run |
| `viewer_published` | `true` once an `iiif.json` PUT has actually succeeded — interim or final. What the frontend's "open in the viewer" link switches on, never a page count |
| `started_at`, `updated_at` | ISO 8601 UTC |

The **incremental `iiif.json`** is the other half of the same idea: every 10
pages (`progress.PUBLISH_EVERY_PAGES`) the wrapper republishes the viewer
manifest with the pages finished so far, so a 638-page volume is readable in
the viewer at page 10 instead of at page 638. Only the dimensions this run
holds in memory go into it — reading a resumed run's earlier ALTOs back would
be one S3 GET per page in the middle of the page loop — so on a resumed run
(whose `pages_done` counts pages this process never touched) the interim
publish is skipped outright whenever those in-memory dimensions cover fewer
pages than `pages_done` says are finished, rather than overwrite a complete
`iiif.json` with one naming only the pages since resume; the final publish
reads the resumed pages' ALTO back and always writes the complete one.

## Live status: the read API, not a file

`GET /api/v1/jobs` and `GET /api/v1/jobs/{namespace}/{name}` (D8) are the
whole story: no status *document* is written anywhere — every response is
computed live from the Job/Pod/ConfigMap state, plus the volumes' own
`progress.json` for the rows it answers with (memoized a few seconds, never
persisted):

- **Campaign summary**: `namespace`, `name`, `pipeline`, `phase`
  (`Queued`/`Paused`/`Running`/`Succeeded`/`PartiallyFailed`/`Failed`,
  derived from the Job's `suspend` flag and its `Complete`/`Failed`
  conditions — `PartiallyFailed` is the `Failed` condition with a non-empty
  `completedIndexes`), `counts` (`total` = `completions`, `active`, `done` =
  `|completedIndexes|`, `failed` = `|failedIndexes|`), `suspended`,
  `createdAt`, `resultsBase`, and `warmup` (`{phase, reason?}` — the
  pipeline's warm-up Job, matched by namespace + pipeline label).
- **Per-volume detail** (paged by index, `offset`/`limit`): one row per line
  of the campaign's `volumes.txt` ConfigMap — `index`, `id`, `state`
  (`done`/`failed`/`active`/`pending`), `manifestUrl`/`iiifUrl`/`altoPrefix`
  (built from `resultsBase`), `sourceUrl` (the URL half of the `volumes.txt`
  line; absent for an `images:` volume, which has no manifest), `progress`
  (below), `logUrl` —
  an absolute URL
  (`<public_results_base>/status/logs/<pipeline>/<id>.txt`, unconditional;
  bucket-root, no namespace/S3_PREFIX prefix, since the browser has no
  bucket base URL to resolve a bare key against) — and `reason`, the failed
  pod's own termination message parsed into `{stage, permanent, error}`,
  present only while a pod for that index still exists.
- **Per-volume progress**: `progress` is `{done, total, failed, lastPage,
  stage, updatedAt, ageSeconds, lastError, errors, viewerPublished}`, or
  `null` when nothing is known — read by the API from that volume's
  `progress.json` (from `manifest.json` for a volume finished before that
  file existed). `ageSeconds` is computed by the API from its own clock at
  fetch time, not left for the browser to derive from `updatedAt`, so a
  reader's clock skew cannot make a row read "0 s ago". A `pending` volume is
  never fetched, and a bucket that does not answer is `null`, never a 500.
  Fetched at most `PROGRESS_FETCH_CAP` (32) rows per request, `active` ones
  first, so a large `limit` cannot turn into hundreds of sequential GETs
  through the API's one HTTP client — a row past the cap simply carries no
  `progress`.
- **Failures**: up to 50 of the most recent failed-with-a-reason rows,
  included in the detail response.
- **Detail-only, computed over every volume** (not just the requested page):
  `latest` — the volume a folded card shows, the newest `active` row else
  the newest `done` one — and `pipelineSteps` / `pipelineYaml`, read from
  the campaign's `htr-pipeline-<id>` ConfigMap.
- **Detail-only, summed over the volumes the response carries**: `pagesDone`,
  `pagesTotal`, `pagesFailed`, `errors`, and `lastError` — the most recent
  page failure among them, with the `volume` it happened in and that volume's
  `logUrl`, so the campaign card can link to a run log for a row that is not
  on the page being shown.

Full field derivation: [`packages/web/src/htrflow_web/projection.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/web/src/htrflow_web/projection.py).
The frontend consumes this shape directly — see [Campaign Browser](frontend.md).
