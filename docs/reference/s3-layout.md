# S3 Layout

Everything the system writes lands in one bucket (default `htr-results`).
Results are namespaced `<namespace>/<pipeline>/<volume>/` — the namespace
comes from `S3_PREFIX`, which the converter always sets to the campaign's
namespace, and the pipeline id is part of the key, so re-running a volume
under a new recipe never overwrites old results. Campaign state is computed
live by the read API ([Web front & read API](web.md)); the one thing the
cluster cannot answer, how far a running volume has got, the wrapper writes
beside its results as `progress.json`.

## Key layout

```
<namespace>/<pipeline>/<volume>/
  page/<page>.xml            # per-page PAGE XML, uploaded FIRST (wrapper)
  alto/<page>.xml            # per-page ALTO, uploaded second — "page done" (wrapper);
                             # both carry x-amz-meta-source-digest, the digest of
                             # the source image they were made from (resume reads it)
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
root. Nothing else in this system writes to S3 at all. The read API only
reads `progress.json` and `manifest.json`, through its own address for the
bucket ([View Results](../getting-started/viewing.md)).

Anonymous read and listing are set by the bucket policy, described in
[Security](../how-it-works/security.md#the-bucket-policy): with the
devstack's store, everything is readable except `status/logs/*` when
`rustfs.publicLogs` is `false`, and listing is always denied.

## `manifest.json` (completion marker)

Written only after the verify gate confirms every page is accounted for:
**both** PAGE and ALTO in S3, skipped by resume, or recorded as failed with
a reason. A volume can therefore be done and still have lost pages, which is
what `pages_ok` and `pages_failed` are for: a page that keeps failing does
not hold back the rest of the volume, and the failure stays visible, page by
page. Its presence *is* "done" for that pipeline id — the canonical way to
check status past a Job's `ttlSecondsAfterFinished` is listing
`manifest.json` keys directly. The read API still shows a campaign whose Job
has been reaped, from the campaign's ConfigMap and the status ConfigMap
beside it
([The record a campaign leaves](../how-it-works/campaigns.md#the-record-a-campaign-leaves)),
but per-volume detail past the TTL comes from the bucket.

A retry that is about to redo pages which already have files deletes the
previous `manifest.json` first, then `iiif.json`, and only then those pages'
stale PAGE and ALTO. So a reader never finds a completion marker describing
pages that are gone, nor a `manifest.json` without its `iiif.json`; the run's
own publish writes both again at the end.

| Field | Meaning |
|---|---|
| `volume`, `pipeline_id` | the key pair |
| `pipeline_sha256` | sha256 of the `pipeline.yaml` text the pod was given — matches the `pipeline-sha256` annotation the converter puts on `htr-pipeline-<id>` at render time |
| `pipeline_yaml` | that text |
| `image_digest` | the `IMAGE_DIGEST` env (the pipeline's digest pin); `"unknown"` when the pod was not given one |
| `htrflow_version` | `importlib.metadata.version("htrflow")` in the image |
| `pages` | canvas count |
| `pages_ok`, `pages_failed` | how the volume came out: `pages_ok` + `pages_failed` + the pages resume skipped = `pages`. `pages_failed > 0` on a volume that is nonetheless done — every one of those pages is in `results` with its `error` |
| `results` | `{"0001": {"status": "ok" \| "failed" \| "skipped", "seconds", "error"?}, …}` |
| `page_sources` | `{"0001": <source image URL, userinfo/query stripped>, …}` — for a reader, not for the comparison |
| `page_source_digests` | `{"0001": <sha256 hex>, …}` — what resume compares: the full source URL with its credentials removed (userinfo, the `X-Amz-*` presign parameters, `token`, `sig`, `signature`, `key`), hashed. The redacted URL above has lost its query, so on a host that selects the image with `?id=` every page of a volume looks the same; a digest keeps the query without publishing it |
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
error. It says nothing about completion — `manifest.json` alone does that,
and is written last. A run that fails in its `config` stage writes none at
all: the bucket and the prefix this key lives under are themselves settings,
so until they parse there is nowhere to put it — that failure is read from the
termination message and the pod's log instead.

| Field | Meaning |
|---|---|
| `stage` | `setup`, `resume`, `load`, `stream`, `verify`, `publish`, `done` — the wrapper's own stage names — or `failed`, written on the way out of a run that did not finish. A run stopped by a SIGTERM is the one exception: it leaves the stage it was in, so the little time the pod has left goes to shipping the run log rather than to a status write. The termination message still names the stage. There is no `config` here: the tracker is built after that stage, so a `ConfigError` leaves no `progress.json` at all |
| `pages_total` | canvases in the manifest this run covers |
| `pages_done` | pages in the bucket: this run's `ok` pages **plus** the ones a previous run finished and resume skipped |
| `pages_failed` | pages this run recorded as failed |
| `last_page` | the page whose outcome was recorded last |
| `last_error` | `{"page", "error"}` for the most recent failed page — the wrapper's own sentence, URL-redacted and capped at 300 characters — or `null` |
| `errors` | ERROR-and-worse log records so far, counted as they are emitted. Not WARNING: the wrapper logs its own benign warnings (a pipeline rebuild after a dead worker thread, "viewer manifest covers n/m pages") that must not light a "something went wrong" chip on a healthy run |
| `viewer_published` | `true` once an `iiif.json` PUT has actually succeeded — interim or final. What the frontend's "open in the viewer" link switches on, never a page count |
| `started_at`, `updated_at` | ISO 8601 UTC |

The **interim `iiif.json`**: every 10 pages the wrapper republishes the
viewer manifest with the pages finished so far, so a long volume opens in
the viewer early. A resumed run skips the interim publish until it covers
every finished page; the final publish always writes the complete one
([From Image to Transcription](../how-it-works/page-flow.md)).

## Live status

Nothing campaign-level is written to the bucket. The read API computes each
campaign's phase, counts and per-volume rows live from the cluster, reading
only `progress.json` (or `manifest.json`) here, and keeps a short summary in
the cluster as the `campaign-<name>-status` ConfigMap. The fields are in
[Web front & read API](web.md).
