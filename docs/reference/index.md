# Reference

Curated reference for the htrflow-batch workspace — the environment contracts,
YAML schemas, S3 layout, and chart values that the narrative pages link to.
Each page summarizes the key surface and links to the source; the source
docstrings remain the authoritative signature reference.

## Pages

| Page | Description |
|------|-------------|
| [Campaign & Pipeline YAML](campaign-yaml.md) | The files in the campaigns repo — what the converter parses, renders and rejects |
| [Wrapper](wrapper.md) | `htrflow-batch` package — the env contract and the modules behind a batch pod |
| [Chart Values](chart.md) | `charts/htrflow-batch` — every `values.yaml` key and the objects it renders |
| [S3 Layout](s3-layout.md) | Every key the system writes to the results bucket |
| [Campaign Browser](frontend.md) | The SvelteKit SPA — config, derivation rules, build and test commands |

## Packages

The repo is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
with three Python packages under `packages/*`, a TypeScript frontend, and a
Helm chart:

| Component | Path | Runs as |
|-----------|------|---------|
| Wrapper (`htrflow-batch`, module `htrflow_batch`) | `packages/wrapper/` | The container of every batch pod — fetch, transcribe, stream results to S3 |
| Converter (`htrflow-converter`, CLI `htrflow-campaigns`) | `packages/converter/` | Pure function: campaign/pipeline YAML → Kubernetes manifests, run in the campaigns repo's own CI (no image, `uvx` install) |
| Web front (`htrflow-web`, module `htrflow_web`) | `packages/web/` | One Deployment: read-only `GET /api/v1/jobs` over the Indexed Jobs a campaign renders to, plus the campaign browser and Universal Viewer as static files |
| Campaign browser | `frontend/` | Static SPA built into the web image, served at `/` |
| Chart | `charts/htrflow-batch/` | Kueue objects, the web front, model cache PVC, NetworkPolicies |
