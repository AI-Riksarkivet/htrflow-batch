# htrflow-web

The system's web front: the read API **and** the site it serves. A small
FastAPI service that lists the campaign Indexed Jobs in its namespace and
projects them, together with their `volumes.txt` ConfigMap and Pods, onto
`GET /api/v1/jobs` — and mounts the built campaign browser, Universal Viewer
included, on everything else. It is the only source the browser reads: the
Job's `completedIndexes` and `failedIndexes` are the progress, the Pod
termination messages are the failure reasons, and every result link is built
from the public results base. Read-only by construction: the code only calls
get and list, the chart grants get/list/watch, and a test greps the source to
keep it that way. There is no authentication.

Until B63 Task 17 the site was a separate nginx image proxying `/api/`
here; one Deployment, one Service and one image do it now.

- Design: [Campaigns as Indexed Jobs](../../docs/superpowers/specs/2026-09-01-indexed-jobs-design.md),
  decision D8
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
| `GET /`, `/log`, `/uv.html`, `/config.js`, … | The built site from `HTRFLOW_WEB_STATIC` (mounted last, so no file can shadow an API route). Extensionless paths resolve to adapter-static's `<route>.html`, which is how `/log` works on a refresh |
| `GET /api/v1/jobs` | One `JobSummary` per campaign Job, newest first: namespace, name, pipeline, phase, counts, suspended, createdAt, resultsBase |
| `GET /api/v1/jobs/{namespace}/{name}?offset=0&limit=200` | `JobDetail`: the summary plus `volumes` (one row per index, paged, `limit` at most 1000) and `failures` (the 50 highest failed indexes that have a reason) |

Phase is derived from the Job: `Succeeded` or `Failed` from its conditions,
otherwise `Queued` or `Paused` when suspended (no index done yet, or some),
else `Running`. Each volume row carries `manifestUrl`, `iiifUrl`,
`altoPrefix` under the results base and `logUrl` under the shared
`status/logs/` tree. Only Jobs labelled `app=htrflow-batch` and
`managed-by=converter` are listed, which excludes the warm-up Jobs.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `HTRFLOW_PUBLIC_RESULTS_BASE` | required | Browser-reachable base every result URL is built from |
| `HTRFLOW_NAMESPACES` | own namespace in-cluster, else `htr-batch` | Comma-separated namespaces to list; the chart leaves it unset |
| `HTRFLOW_WEB_STATIC` | `/app/static` | The built site. Missing directory = API only, which is what a local run gets |

The chart sets the first from `publicResultsBase`; the image bakes the
second's default.

## Modules

| Module | Role |
|---|---|
| `app.py` | `create_app(reader, static_dir=None)`: the three routes over a duck-typed reader (so tests wire a fake), the three security headers the old nginx sent, then the static mount |
| `kube.py` | `Config.from_env`, `Reader`: raw-JSON get/list against Jobs, ConfigMaps and Pods, in-cluster or kubeconfig |
| `projection.py` | Pure functions from API-server dicts to `JobSummary` and `JobDetail`; `parse_index_ranges` for `completedIndexes` |
| `__main__.py` | The `htrflow-web` console script: uvicorn on `0.0.0.0:8081` |

## Tests

`test_projection.py` feeds hand-built Job, ConfigMap and Pod dicts to the pure
functions; `test_app.py` drives the routes with a fake reader through
FastAPI's test client; `test_static.py` builds a temporary site directory and
checks the pages, the headers and that `/api/v1/jobs` still wins over a file
of the same name. Nothing touches a cluster.
