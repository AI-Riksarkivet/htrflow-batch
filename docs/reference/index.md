# Reference

The exact contracts behind the narrative pages: environment variables, YAML
schemas, the S3 layout and the chart values. Each page summarizes one surface
and links to its source; the source docstrings stay the authoritative
signature reference.

## Pages

| Page | Description |
|------|-------------|
| [Configuration](configuration.md) | Every setting of every surface, generated from the three config models and the chart values |
| [Campaign & Pipeline YAML](campaign-yaml.md) | The files in a campaigns repo: what the converter parses and refuses |
| [htrflow-campaigns CLI](cli.md) | `init`, `validate`, `render`, `apply`: what `apply` does in order, Argo CD, pausing, pruning, exit codes |
| [Rendered objects](rendered.md) | A worked render, field by field |
| [Wrapper](wrapper.md) | The batch pod's contract: `volumes.txt`, environment, exit codes |
| [Chart Values](chart.md) | `charts/htrflow-batch` values and the objects they render |
| [S3 Layout](s3-layout.md) | Every key the system writes to the results bucket |
| [Web front & read API](web.md) | Routes, API fields, phases, what a campaign card shows, configuration |

## Packages

The repo is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/)
with three Python packages under `packages/*`, a TypeScript frontend, and a
Helm chart:

| Component | Path | Runs as |
|-----------|------|---------|
| Wrapper (`htrflow-batch`, module `htrflow_batch`) | `packages/wrapper/` | The container of every batch pod — fetch, transcribe, stream results to S3 |
| Converter (`htrflow-converter`, CLI `htrflow-campaigns`) | `packages/converter/` | Pure function: campaign/pipeline YAML → Kubernetes manifests. Rendered in the campaigns repo's CI (`uvx` install); applied by hand or by the Argo CD hook, which runs the `htrflow-campaigns` image in the cluster |
| Web front (`htrflow-web`, module `htrflow_web`) | `packages/web/` | One Deployment: `GET /api/v1/jobs` over the Indexed Jobs a campaign renders to, writing only each campaign's status ConfigMap, plus the campaign browser and Universal Viewer as static files |
| Campaign browser | `frontend/` | Static SPA built into the web image, served at `/` |
| Chart | `charts/htrflow-batch/` | Kueue objects, the web front, model cache PVC, NetworkPolicies, Kyverno policies |
