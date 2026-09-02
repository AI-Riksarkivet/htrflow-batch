# htrflow-converter

The `htrflow-campaigns` CLI. It reads a campaigns git repo (`converter.yaml`,
`campaigns/*.yaml`, `pipelines/*.yaml`), validates it, and renders plain
Kubernetes manifests: one ConfigMap plus one warm-up Job per pipeline, and one
`volumes.txt` ConfigMap plus one Indexed Job per campaign. A campaign **is**
its Indexed Job: each volume is one completion index, the Job carries the
Kueue queue label, and pausing or deleting a campaign is a change to its YAML
file. Nothing here talks to a cluster; `kubectl apply` or Argo CD does that
with the rendered files.

- Design: [Campaigns as Indexed Jobs](../../docs/superpowers/specs/2026-09-01-indexed-jobs-design.md),
  §3 for the objects rendered here
- Narrative: [How it works → Campaigns](../../docs/how-it-works/campaigns.md)
- Reference: [Campaign & Pipeline YAML](../../docs/reference/campaign-yaml.md)
  (every field with its default)
- A repo in shape: [`examples/campaigns/`](../../examples/campaigns/README.md)

## Commands

Run from the repo root. It is a uv workspace, and a plain `uv sync` inside
this directory prunes the shared venv down to the root.

```bash
make install                                          # uv sync --all-packages
uv run --all-packages pytest -q packages/converter    # this package's unit tests
uv run htrflow-campaigns validate <campaigns-repo>    # exit 1 and one line per problem
uv run htrflow-campaigns render <campaigns-repo> --out <dir>
make campaigns-apply DIR=<campaigns-repo> [PRUNE=1]   # render + kubectl apply + Kueue pause sync
```

`render` writes `<dir>/pipelines/<id>.yaml` and `<dir>/campaigns/<name>.yaml`
(or `<name>-partN.yaml` above 10 000 volumes) and removes any file in those
two directories it did not write, so a deleted campaign file takes its
manifest with it. It refuses an `--out` that is, or contains, the campaigns
repo itself. Campaigns are append-only: changing the volume list of a campaign
that has already been rendered is an error. Create a new campaign instead.

## Inputs

| File | Parsed by | Rendered as |
|---|---|---|
| `converter.yaml` | `ConverterConfig` (unknown keys rejected, all fields optional) | Namespace, queue, window cap, S3 Secret, model-cache PVC, runtime class, wrapper byte caps, the default pod deadline (`max_seconds` → `activeDeadlineSeconds`), image allow-list |
| `pipelines/<id>.yaml` | `Pipeline` (digest-pinned `image`, htrflow `steps`, optional `model_revision`, `max_seconds`) | ConfigMap `htr-pipeline-<id>` with the pipeline YAML and its sha256; Job `htr-warmup-<id>` |
| `campaigns/<name>.yaml` | `Campaign` (`pipeline`, `volumes`, optional `priority`, `window`, `suspend`) | ConfigMap `campaign-<name>` with `volumes.txt`; Indexed Job `<name>` with `completions = len(volumes)` |

A volume is either a bare id (the manifest URL comes from
`source_template`) or a mapping with `id` and exactly one of `manifest` or
`images`. Validation collects every problem before failing, so one run shows
the whole list.

## Modules

| Module | Role |
|---|---|
| `cli.py` | `validate` and `render` subcommands, the append-only check, pruning, the unsafe `--out` guard |
| `parse.py` | YAML files to domain types via `Model.model_validate`; flattens `pydantic.ValidationError` into one-line problems; `ValidationError`; the cross-file unknown-pipeline check |
| `models.py` | `Volume`, `Campaign`, `Pipeline`, `ConverterConfig` (frozen pydantic models) with all validation rules as field/model validators; `Pipeline.sha256` |
| `render.py` | Patch the packaged skeletons into concrete objects; labels, Kueue queue and priority, env for the wrapper, the 10 000-volume split |
| `manifests/` | The four YAML skeletons: `configmap.yaml`, `campaign-job.yaml`, `pipeline-configmap.yaml`, `warmup-job.yaml` |

## Tests

`tests/fixtures/good` and `tests/fixtures/bad` are small campaigns repos;
`tests/golden/` holds the expected rendered objects for the good one. Rendering
changes show up as golden diffs, which is the intended review surface.
`test_packaging.py` builds the real wheel and installs it into a throwaway
venv to prove the `manifests/` skeletons ship with the package.
