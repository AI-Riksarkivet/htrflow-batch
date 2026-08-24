# Reference

Curated reference for the htrflow-batch workspace — the environment contracts,
YAML schemas, S3 layout, and chart values that the narrative pages link to.
Each page summarizes the key surface and links to the source; the source
docstrings remain the authoritative signature reference.

## Pages

| Page | Description |
|------|-------------|
| [Campaign & Pipeline YAML](campaign-yaml.md) | The two file formats in the campaigns repo — what the reconciler parses and rejects |
| [Reconciler](reconciler.md) | `htrflow-reconciler` package — modules, settings env, volume states, Job naming |
| [Wrapper](wrapper.md) | `htrflow-batch` package — the env contract and the modules behind a batch Job |
| [Chart Values](chart.md) | `charts/htrflow-batch` — every `values.yaml` key and the objects it renders |
| [S3 Layout & status.json](s3-layout.md) | Every key the system writes to the results bucket, and the `status.json` schema |
| [Campaign Browser](frontend.md) | The SvelteKit SPA — config, derivation rules, build and test commands |

## Packages

The repo is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
with two Python packages under `packages/*`, a TypeScript frontend, and a Helm
chart:

| Component | Path | Runs as |
|-----------|------|---------|
| Wrapper (`htrflow-batch`, module `htrflow_batch`) | `packages/wrapper/` | The container of every batch Job — fetch, transcribe, stream results to S3 |
| Reconciler (`htrflow-reconciler`, module `htrflow_reconciler`) | `packages/reconciler/` | A CronJob — reconciles the campaigns git repo against S3 and the cluster |
| Campaign browser | `frontend/` | Static SPA in the viewer image, served at `/` |
| Chart | `charts/htrflow-batch/` | Kueue objects, viewer, reconciler, PoC dev stack |
