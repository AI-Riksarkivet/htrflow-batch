# htrflow-web

The system's web front: the read API **and** the site it serves. A small
FastAPI service that lists the campaign Indexed Jobs in its namespace and
projects them, together with their `volumes.txt` ConfigMap and Pods, onto
`GET /api/v1/jobs` — and mounts the built campaign browser, Universal Viewer
included, on everything else. It is the only source the browser reads: the
Job's `completedIndexes` and `failedIndexes` are the progress, the Pod
termination messages are the failure reasons, and every result link is built
from the public results base; how far each volume has got is read from its
`progress.json` in the results bucket. Read-only but for one write: every call
is a get or a list against Jobs, Pods and ConfigMaps, except the server-side
apply of each campaign's status ConfigMap (`campaign-<name>-status`), which
keeps a campaign on the page once its Job is past `ttlSecondsAfterFinished`.
The chart's Role grants get/list on Jobs and Pods and get/list/create/patch on
ConfigMaps (a server-side apply of an object that does not exist yet is a
create), no watch; a test greps the source for any other create, patch,
replace or delete call. There is no authentication.

The site used to be a separate nginx image proxying `/api/` here; one
Deployment, one Service and one image do it now.

- Design: [Campaigns as Indexed Jobs](../../docs/superpowers/specs/2026-09-01-indexed-jobs-design.md),
  its decision on the read API
- Consumer: [Campaign browser](../../frontend/README.md) and the
  [frontend reference](../../docs/reference/frontend.md)
- Deployment: the `htrflow-web` Deployment and Service (NodePort) in
  [`charts/htrflow-batch`](../../charts/htrflow-batch/README.md)

## Commands

Run from the repo root. It is a uv workspace, and a plain `uv sync` inside
this directory prunes the shared venv down to the root.

```bash
make install                                    # uv sync --all-packages
uv run --all-packages pytest -q packages/web    # this package's unit tests
HTRFLOW_PUBLIC_RESULTS_BASE=https://results.example.org uv run htrflow-web   # :8081, uses your kubeconfig
make build-web                                  # the image, .docker/htrflow-web.dockerfile
make scan-web                                   # Trivy, HIGH/CRITICAL with a fix fail
```

Outside a cluster the reader falls back from the in-cluster service account to
your kubeconfig, so a local run shows the Jobs of whatever `HTRFLOW_NAMESPACES`
names.

## Endpoints

| Route | Returns |
|---|---|
| `GET /healthz` | `{"ok": true}` |
| `GET /api/v1/version` | `{"version": "<release tag>", "web": "<package version>"}` — the tag the image was published under (`HTRFLOW_BATCH_VERSION`, baked in by the dockerfile; `dev` outside an image), which is what the page's header shows, and beside it this package's own version. Answers in site-only mode too |
| `GET /`, `/log`, `/alto`, `/uv.html`, `/config.js`, … | The built site from `HTRFLOW_WEB_STATIC` (mounted last, so no file can shadow an API route). Extensionless paths resolve to adapter-static's `<route>.html`, which is how `/log` and `/alto` work on a refresh |
| `GET /api/v1/jobs?reaped=20` | One `JobSummary` per campaign, newest first: namespace, name, pipeline, phase, counts, suspended, createdAt, finishedAt, resultsBase, warmup, jobGone. Every live campaign Job, plus the `reaped` newest campaigns whose Job is gone (default 20, at most 10000), drawn from their two ConfigMaps; the `X-Reaped-Total` header says how many of those there are in all |
| `GET /api/v1/jobs/{namespace}/{name}?offset=0&limit=200` | `JobDetail`: the summary plus `volumes` (one row per index, paged, `limit` at most 1000), `failures` (the 50 highest failed indexes, with a reason or without), `latest` (the newest active volume, else the newest done one), the campaign's page totals from the progress files, and `pipelineSteps`/`pipelineYaml` from the `htr-pipeline-<id>` ConfigMap. All but `volumes` are computed over every volume, not just the requested page. `404` for a name that is no campaign: a Job without the campaign labels is answered as though it were absent |

Phase is derived from the Job: `Succeeded` from its `Complete` condition;
`Failed`, or `PartiallyFailed` when the `Failed` condition arrives with a
non-empty `completedIndexes` (the campaign gave up, but what those indexes
published is there); otherwise `Queued` or `Paused` when suspended (no index
done yet, or some), else `Running`. Each volume row carries `manifestUrl`,
`iiifUrl`, `altoPrefix` under the results base, `logUrl` under the shared
`status/logs/` tree, and `sourceUrl` — the URL half of its `volumes.txt`
line, null for an `images:` volume or for a URL a browser's URL parser would
refuse. Only Jobs labelled `app=htrflow-batch` and `managed-by=converter` are
listed, which excludes the warm-up Jobs — those are read separately
(`app=htrflow-warmup`) and matched onto each row's `warmup` field by
namespace + pipeline label; a failed match costs one extra pod list, for the
warm-up's termination message as `reason`.

A detail request lists the campaign's pods that are not `Succeeded` — a
succeeded pod belongs to a done index, which the Job's `completedIndexes`
already says — a page at a time, keeping only the fields the projection reads.
Every call to the API server has a connect and read timeout, and `/healthz`
is answered on the event loop, so requests stuck on a hung API server cannot
fail the readiness probe.

**The status ConfigMap.** Both routes write what they observed into
`campaign-<name>-status` (merged over what is stored, never shrinking it, and
not sent at all when nothing changed). The summary fields go under the field
manager `htrflow-web`; `failedVolumes` — the failure reasons, which only the
detail route can see because only it reads pods — goes under a manager of its
own, `htrflow-web-failures`, and only the detail route writes it, with the
Job it is about beside it (`failedVolumesJobUid`): the failures of an earlier
Job of the same name are never read or merged as this one's. That write is
held to the ConfigMap the request read, by its uid, so it can never re-create
a record a prune has deleted. Once
`htrflow-campaigns apply` has recorded a campaign's ending, those fields are
its, and this service sends only the ones it does not own.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `HTRFLOW_PUBLIC_RESULTS_BASE` | required | Browser-reachable base every result URL is built from |
| `HTRFLOW_INTERNAL_RESULTS_BASE` | the public base | Where this pod reaches the results bucket to read progress files, when the browser's address does not work from inside the cluster (a `localhost` forward, say) |
| `HTRFLOW_NAMESPACES` | own namespace in-cluster, else `htr-batch` | Comma-separated namespaces to list; the chart leaves it unset |
| `HTRFLOW_WEB_STATIC` | `/app/static` | The built site. Missing directory = API only, which is what a local run gets |
| `HTRFLOW_WEB_SITE_ONLY` | unset | Any non-empty value: serve the site without a cluster — `/api/v1/…` answers `503`, nothing tries to load a kubeconfig. The local compose stack runs this way |
| `HTRFLOW_BATCH_VERSION` | `dev` | The release this image is: baked in from the publish tag by `.docker/htrflow-web.dockerfile`, reported by `/api/v1/version` and shown in the page header. Set by the image, never by an operator |

All six are read in one place — `kube.Config`, a frozen pydantic model whose
fields carry their own env name (`Field(alias=...)`), the same idiom the
wrapper and the converter use. `app.py` and `__main__.py` read no environment
of their own. The chart sets the first from `publicResultsBase` and the second
from `web.internalResultsBase`; `HTRFLOW_WEB_STATIC` empty means the directory
the image bakes in.

**Why the `HTRFLOW_` prefix here and bare names in the wrapper.** The first
five are an operator's settings for a long-lived service that shares a pod
environment with whatever the platform sets, so they are namespaced. The
wrapper's (`PUBLIC_RESULTS_BASE`, `S3_BUCKET`, …) are an in-pod contract
written by the rendered Job itself
(`packages/converter/src/htrflow_converter/manifests/campaign-job.yaml`):
nothing else writes that pod's environment, and renaming them would break
every campaign Job in flight. Neither surface ever carries a secret — see
[Configuration reference](../../docs/reference/configuration.md).

## Modules

| Module | Role |
|---|---|
| `app.py` | `create_app(reader, static_dir=None)` (`__main__` passes `cfg.static_dir`): the routes over a reader that meets `kube.ReaderLike` (so tests wire a fake), the security headers — on every response, errors included — and the Content-Security-Policy each served document gets (the viewer's own, the SPA's `connect-src`, a strict one for any other page), then the static mount. `NoCluster` is the site-only reader |
| `kube.py` | `Config` (the whole env contract) and `Reader`: raw-JSON get/list against Jobs, ConfigMaps and Pods, and the status ConfigMap's server-side apply, in-cluster or kubeconfig |
| `projection.py` | Pure functions from API-server dicts to `JobSummary` and `JobDetail`, and what to write to the status ConfigMap under which field manager; `parse_index_ranges` for `completedIndexes` |
| `progress.py` | `ProgressReader`: each volume's `progress.json` (or, for an older run, `manifest.json`) from the results bucket, size-capped, never inflated, cached a few seconds for a running volume and an hour for a finished one |
| `__main__.py` | The `htrflow-web` console script: uvicorn on `0.0.0.0:8081`; picks the reader (`kube.Reader`, or `NoCluster` under `HTRFLOW_WEB_SITE_ONLY`) |

## Tests

`test_projection.py` feeds hand-built Job, ConfigMap and Pod dicts to the pure
functions; `test_app.py` drives the routes with a fake reader through
FastAPI's test client; `test_static.py` builds a temporary site directory and
checks the pages, the headers, HEAD on every route, site-only mode's 503s,
and that `/api/v1/jobs` still wins over a file of the same name. Nothing touches a cluster.
