# Web front & read API

One Deployment, `htrflow-web`, serves three things on one origin:

- the **campaign browser**, a static single-page app, at `/`
- **Universal Viewer** at `/uv.html`
- the **read API** at `/api/v1/…`

The read API computes every answer live from the campaign Jobs, their Pods
and ConfigMaps, plus each running volume's `progress.json` in the bucket.
It writes one thing: each campaign's status record
([Campaigns → The record](../how-it-works/campaigns.md#the-record-a-campaign-leaves)).
It holds no cluster credentials for the browser and has no authentication:
who may reach it is decided by the network
([Deploy](../getting-started/deploy.md)).

Source: [`packages/web`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/packages/web)
(the API) and [`frontend/`](https://github.com/AI-Riksarkivet/htrflow-batch/tree/main/frontend)
(the browser). Their READMEs are the developer documentation.

## Routes

| Route | What it is |
|---|---|
| `/` | The campaign list: one card per campaign. |
| `/log?log=<url>&manifest=<url>[&live=1]` | The **run viewer**: one volume's run log grouped by stage, and a summary of its `manifest.json` (counts, timings, slowest and failed pages, a per-page grid). With `live=1` it follows the log until the run ends. |
| `/alto?src=<url>` | The **ALTO viewer**: one page's text in reading order, tinted by confidence, with a raw-XML toggle. |
| `/uv.html#?manifest=<url>` | Universal Viewer on a IIIF manifest. |
| `/config.js` | The browser's configuration, written by the API from its own environment (below). |
| `GET /healthz` | `{"ok": true}`. |
| `GET /api/v1/version` | `{"version", "web"}`: the release tag both images carry, and the web package's version. |
| `GET /api/v1/jobs?reaped=20` | One summary per campaign (below). |
| `GET /api/v1/jobs/{namespace}/{name}?offset=0&limit=200` | One campaign's detail (below). `404` for a name that is not a campaign. |

`/log` and `/alto` open only URLs under the results base, so a mailed link
cannot point them at another host. How to reach these from a browser is in
[View Results](../getting-started/viewing.md).

## The campaign list: `GET /api/v1/jobs`

Every live campaign Job in the namespace, plus the `reaped` newest
campaigns whose Job is gone (default 20, at most 10 000). The
`X-Reaped-Total` header says how many reaped campaigns there are in all.
Each row is a `JobSummary`:

| Field | Meaning |
|---|---|
| `namespace`, `name`, `pipeline` | Which campaign. |
| `phase` | See [Phases](#phases-and-volume-states). |
| `counts` | `total` (= `completions`), `active`, `done` (= completed indexes), `failed` (= failed indexes). |
| `suspended` | The Job's `spec.suspend`. |
| `createdAt`, `startedAt`, `finishedAt` | Timestamps; `finishedAt` is set once the campaign has ended. |
| `resultsBase` | `<public results base>/<namespace>/<pipeline>`. |
| `warmup` | `{phase, reason?}` from the pipeline's warm-up Job: `missing`, `pending`, `running`, `succeeded` or `failed` (with a `reason`). |
| `jobGone` | `true` for a campaign whose Job is past its TTL. The row comes from the campaign's two ConfigMaps. |

## One campaign: `GET /api/v1/jobs/{namespace}/{name}`

A `JobDetail` is the summary plus:

| Field | Meaning |
|---|---|
| `volumes` | One row per index, paged by `offset` and `limit` (default 200, at most 1000). |
| `failures` | Up to 50 of the newest failed rows, with a reason or without. |
| `latest` | The newest active volume, else the newest done one, else `null`. |
| `pipelineSteps`, `pipelineYaml` | The step names and the steps document, from the `htr-pipeline-<id>` ConfigMap. Empty when it is gone. |
| `pagesDone`, `pagesTotal`, `pagesFailed`, `errors` | Page counts summed over every volume whose progress has been read. |
| `lastError` | The most recent page failure among them, with its `volume` and that volume's `logUrl`. |
| `pagesCoverage` | `{counted, of}`: how many run volumes those sums cover. It reaches `of` over a few polls. |

Everything but `volumes` is computed over every volume, not only the
requested page. A campaign of thousands shows its first 200 rows, and the
volume in flight is rarely among them.

Each volume row (`VolumeView`):

| Field | Meaning |
|---|---|
| `index`, `id` | The index and its line of `volumes.txt`. |
| `state` | See [Phases](#phases-and-volume-states). |
| `manifestUrl`, `iiifUrl`, `altoPrefix` | Result URLs under `resultsBase`. |
| `sourceUrl` | The URL half of the `volumes.txt` line. `null` for an `images:` volume, or for a URL a browser could not open. |
| `logUrl` | `<public results base>/status/logs/<pipeline>/<id>.txt`, always present. |
| `reason` | `{stage, permanent, error}` from a failed pod's termination message, while a pod for that index still exists. |
| `progress` | From the volume's `progress.json` (below), or `null`. |

**Where a `reason` comes from.** The wrapper's own JSON termination message
when it wrote one. A pod stopped by the warm-up gate carries the gate's
message. A pod killed at its deadline reads `"error": "DeadlineExceeded"`.
A pod that left no message reads its own reason (`Evicted`), else the
container's (`OOMKilled (exit code 137)`).

**`progress`** is `{done, total, failed, lastPage, stage, updatedAt,
ageSeconds, lastError, errors, viewerPublished}`, read from the volume's
`progress.json` (or, for an older run, its `manifest.json` counts). The
fields are described in
[S3 Layout](s3-layout.md#progressjson-live-and-never-a-completion-marker).
`ageSeconds` is computed by the API from its own clock, so a reader's clock
skew never shows "0 s ago".

- A `pending` volume is never read. A bucket that does not answer gives
  `null`, never a 500.
- Answers are cached: 5 s for a running volume, an hour for one that is
  over (an absent file included).
- One request makes at most 100 bucket reads and spends at most 5 s on
  them, active volumes first. The rest come from later polls.
- The API reads the bucket at `HTRFLOW_INTERNAL_RESULTS_BASE` when that is
  set (chart `web.internalResultsBase`). If the pod cannot reach the public
  address, set it, or the page silently shows no progress.

## Phases and volume states

A campaign's `phase` comes from its Job:

| Phase | When |
|---|---|
| `Queued` | Suspended, nothing done yet. |
| `Paused` | Suspended, some indexes done. |
| `Running` | Not suspended and not ended. A campaign whose warm-up has not finished also reads `Running`; the warm-up chip says why nothing moves. |
| `Succeeded` | The Job's `Complete` condition. |
| `PartiallyFailed` | The `Failed` condition with some indexes completed: what they published is there. |
| `Failed` | The `Failed` condition with nothing completed. |
| `Unknown` | Only on a `jobGone` row whose record never reached an ending (the Job was deleted by hand or by a prune). |

A campaign that stays `Queued` with free capacity is covered in
[Queueing](../how-it-works/queueing.md).

A volume's `state` is `pending`, `active`, `done` or `failed`, computed
from the Job's index sets and pods. `unknown` appears only on a `jobGone`
campaign, for volumes whose ending the record does not say.

## Reaped campaigns

A campaign whose Job is past `ttlSecondsAfterFinished` keeps its row, with
`jobGone: true`, the phase and counts its record kept, and `finishedAt`.
Its detail rebuilds the volume rows from `volumes.txt`: a volume named in
the record's `failedVolumes` is `failed` with that sentence, and every
other volume takes the campaign's ending. When the record counts more
failures than it names, those others read `unknown` rather than `done`.
Their links and progress still come from the bucket.

## What a campaign card shows

Every card has the same four zones, in the same order, so ten campaigns
scan like a table. A folded card shows only the first; the rest appear when
it is opened:

1. **Identity and state.** The campaign's name (click to fold or unfold;
   `namespace/name` only when the list spans more than one namespace),
   its phase, the warm-up chip while the warm-up has not succeeded, a
   "job removed" chip for a reaped campaign, and when it was created and
   finished.
2. **The body.** The totals (`volumes`, `pages`) with progress bars, then
   the loaded page of the campaign's volumes, with "load more" for the rest.
3. **Problems**, only when there is one: why the warm-up failed, each
   failed volume as `id: sentence` linked to its run log, and the latest
   page error when its volume is not on screen. Past three sentences the
   rest wait behind "N more".
4. **Provenance.** The pipeline (click for its YAML) and, on the row below,
   the models it loads, each linked to its Hugging Face revision, or marked `unpinned`.

**Each volume row** links:

- **the id** to Universal Viewer: the published `iiif.json` once the volume
  is done or has published an interim one, its source manifest before that,
  and plain text when there is neither
- **a manifest icon** to the source manifest (empty for an `images:` volume)
- **a log icon**, last on the row after its state, to the run viewer, live
  while the volume is not done

**The warm-up chip** reads "warm-up pending", "running", "failed" or "no
warm-up" (`missing`). A `failed` warm-up also colours the card as failed,
since no pod can start without it; its reason is in the chip's title.
`missing` colours the card only while the campaign has not succeeded.

**Paging and polling.** The list re-fetches every 60 s. An open card fetches
its own detail, re-fetching every open page on each poll. A folded card
fetches nothing, except a succeeded campaign's, which is read once for
whether it lost pages. A finished, unknown or reaped campaign is read once,
when its card is first on screen or opened, and not polled again. A failed poll puts a banner over the last list, and
backs off. Older reaped campaigns wait behind "show older campaigns", 20 at
a time.

The page and component internals are in the
[frontend README](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/frontend/README.md).

## Configuration

The browser has no environment of its own. It reads `window.API_BASE` and
`window.RESULTS_BASE` from `/config.js`, which the API writes from its own
environment on each request: `/api/v1`, and `HTRFLOW_PUBLIC_RESULTS_BASE`.
Set `publicResultsBase` on the chart and the page follows; there is no
second copy to keep in step.

| API env var | Default | Meaning |
|---|---|---|
| `HTRFLOW_PUBLIC_RESULTS_BASE` | required | The browser-reachable base every result URL is built from. The chart sets it from `publicResultsBase`. |
| `HTRFLOW_INTERNAL_RESULTS_BASE` | the public base | Where the pod reads progress files, when the browser's address does not work from inside the cluster. Chart `web.internalResultsBase`. |
| `HTRFLOW_NAMESPACES` | the pod's own namespace (`htr-batch` outside a cluster) | Comma-separated namespaces to list. |
| `HTRFLOW_WEB_STATIC` | `/app/static` | The built site; missing means API only. |
| `HTRFLOW_WEB_SITE_ONLY` | unset | Serve the site with no cluster: `/api/v1/…` answers `503`. |
| `HTRFLOW_BATCH_VERSION` | `dev` | Baked into the image from the release tag; shown in the page header. |

The generated [Configuration](configuration.md) page lists every setting.
The chart values are in [Chart Values](chart.md#web-front-web).

## Content-Security-Policy

| Response | Policy |
|---|---|
| Every response | `frame-ancestors 'none'`, with `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`. |
| The SPA's pages | Their own meta CSP from the build (`script-src 'self'` plus the hash of SvelteKit's init script, `object-src 'none'`, `base-uri 'self'`), and a header adding `connect-src 'self' <results base>/`: the page may fetch only from the API and the results bucket. |
| `uv.html` | A policy of its own, chosen by the file served, whatever path reached it: hashed inline script, inline styles, images and manifests from anywhere. |
| Any other HTML document in the site | `default-src 'none'; sandbox`. |

The meta CSP is why configuration arrives as `/config.js` and never as an
inline script. In site-only mode, or with a results base a CSP source
cannot express, the SPA gets no `connect-src` header.
