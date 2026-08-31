# htrflow-batch

> **⚠️ Not for use yet.** This repository is under active development at
> Riksarkivet's AI lab and is not ready for others to run: interfaces,
> chart values and the campaigns format still change without notice, and
> the published images are for our own dev cluster. Watch the releases;
> we will remove this notice when there is a version we stand behind.

Kueue-gated batch HTR platform around the [htrflow](https://github.com/AI-Riksarkivet/htrflow)
image: a thin wrapper streams archival volumes page-by-page from IIIF into
htrflow, verifies every output, and publishes ALTO/PAGE results plus an IIIF
manifest to S3 for viewing — all as plain Kubernetes Jobs under Kueue,
declared in a campaigns git repo and submitted by a reconciler CronJob. No
custom scheduler, no database.

## Quickstart

```bash
make install && make test   # uv workspace sync (packages/*) + the wrapper and reconciler unit tests
cd frontend && bun install && bun run test   # the campaign browser's tests
make compose-up             # local smoke stack: S3 + fixtures + wrapper + viewer, no cluster needed
```

For a real cluster, install the Helm chart (Kueue CRDs and an S3 Secret with
a `credentials` ini key are prerequisites):

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set publicResultsBase=<browser-reachable results base URL> \
  --set viewer.image=<viewer image>@sha256:<digest>
```

then declare volumes in a campaigns repo and enable the reconciler. See
`docs/getting-started/` for prerequisites, deployment, and running a
campaign end to end; `docs/development/local-k3s.md` for the single-node
GPU PoC loop.

## Documentation

Full documentation lives in `docs/` — architecture, the streaming wrapper
design, failure handling, the reference pages (chart values, reconciler and
wrapper env, S3 layout), the Phase 2 roadmap, and development/CI notes.
Serve it locally:

```bash
make docs-serve
```
