# htrflow-batch

Kueue-gated batch HTR platform around the [htrflow](https://github.com/AI-Riksarkivet/htrflow)
image: a thin wrapper streams archival volumes page-by-page from IIIF into
htrflow, verifies every output, and publishes ALTO/PAGE results plus an IIIF
manifest to S3 for viewing — all as plain Kubernetes Jobs under Kueue, no
custom scheduler, no database.

## Quickstart

```bash
make install && make test   # uv workspace sync (packages/*) + wrapper unit tests
make compose-up             # local smoke stack: S3 + fixtures + wrapper + viewer, no cluster needed
```

For a real cluster, install the Helm chart:

```bash
helm install htr charts/htrflow-batch -n htr-batch --create-namespace \
  --set image.repository=<your-registry>/htrflow-batch --set image.tag=<pinned-digest-or-tag>
```

See `docs/getting-started/` for prerequisites, deployment, and running a
volume end to end.

## Documentation

Full documentation lives in `docs/` — architecture, the streaming wrapper
design, the Phase 2 roadmap, and development/CI notes. Serve it locally:

```bash
make docs-serve
```
