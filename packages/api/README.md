# htrflow-api

The read API behind the campaign browser. A small FastAPI service that lists
the campaign Indexed Jobs in its namespace and projects them, together with
their `volumes.txt` ConfigMap and Pods, onto `GET /api/v1/jobs`. It is the
only source the browser reads: the Job's `completedIndexes` and
`failedIndexes` are the progress, the Pod termination messages are the
failure reasons, and every result link is built from the public results base.
Read-only by construction: the code only calls get and list, the chart grants
get/list/watch, and a test greps the source to keep it that way. There is no
authentication; the service sits behind the viewer's nginx on a
cluster-internal path.

- Design: [Campaigns as Indexed Jobs](../../docs/superpowers/specs/2026-09-01-indexed-jobs-design.md),
  decision D8
- Consumer: [Campaign browser](../../frontend/README.md) and the
  [frontend reference](../../docs/reference/frontend.md)
- Deployment: the `htrflow-api` Deployment and Service in
  [`charts/htrflow-batch`](../../charts/htrflow-batch/README.md), proxied by the
  viewer at `/api/`

## Commands

Run from the repo root. It is a uv workspace, and a plain `uv sync` inside
this directory prunes the shared venv down to the root.

```bash
make install                                    # uv sync --all-packages
uv run --all-packages pytest -q packages/api    # this package's unit tests
HTRFLOW_PUBLIC_RESULTS_BASE=https://results.example.org uv run htrflow-api   # :8081, uses your kubeconfig
make build-api                                  # the image, .docker/htrflow-api.dockerfile
make scan-api                                   # Trivy, HIGH/CRITICAL with a fix fail
```

Outside a cluster the reader falls back from the in-cluster service account to
your kubeconfig, so a local run shows the Jobs of whatever `HTRFLOW_NAMESPACES`
names.

## Endpoints

| Route | Returns |
|---|---|
| `GET /healthz` | `{"ok": true}` |
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

The chart sets the first from `publicResultsBase`.

## Modules

| Module | Role |
|---|---|
| `app.py` | `create_app(reader)`: the three routes over a duck-typed reader, so tests wire a fake |
| `kube.py` | `Config.from_env`, `Reader`: raw-JSON get/list against Jobs, ConfigMaps and Pods, in-cluster or kubeconfig |
| `projection.py` | Pure functions from API-server dicts to `JobSummary` and `JobDetail`; `parse_index_ranges` for `completedIndexes` |
| `__main__.py` | The `htrflow-api` console script: uvicorn on `0.0.0.0:8081` |

## Tests

`test_projection.py` feeds hand-built Job, ConfigMap and Pod dicts to the pure
functions; `test_app.py` drives the routes with a fake reader through
FastAPI's test client. Nothing touches a cluster.
