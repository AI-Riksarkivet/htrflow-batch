# Campaign & Pipeline YAML

The campaigns git repo has two directories the reconciler reads:
`campaigns/*.yaml` (what to run) and `pipelines/*.yaml` (how to run it).
The filename stem is the campaign / pipeline id.

Source: [`packages/reconciler/src/htrflow_reconciler/parse.py`](https://github.com/carpelan/test/blob/main/packages/reconciler/src/htrflow_reconciler/parse.py)

## Campaign file — `campaigns/<name>.yaml`

```yaml
pipeline: demo-v1          # required: a pipeline id from pipelines/
volumes:
  # 1) Bare string: a Riksarkivet reference code. The manifest URL is
  #    templated as https://lbiiif.riksarkivet.se/arkis!<id>/manifest
  - R0001203

  # 2) Explicit IIIF manifest (Presentation v2 or v3):
  - id: loc-mal2459400
    manifest: https://www.loc.gov/item/mal2459400/manifest.json

  # 3) Bare image URLs — the reconciler builds and publishes a synthetic
  #    IIIF manifest for them (see S3 Layout: sources/):
  - id: htr-demo-examples
    images:
      - https://example.org/page1.jpg
      - https://example.org/page2.jpg
```

Rules enforced by `parse_campaign`:

| Rule | Consequence when violated |
|------|---------------------------|
| `pipeline:` is required and must name a file in `pipelines/` | Campaign is reported with an error in `status.json`; nothing submits |
| Every volume needs `manifest:` or `images:` (unless it is a bare string) | Campaign error |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` — alphanumeric at both ends, ≤63 chars | Campaign error (`unsafe volume id`) |
| Volume ids are unique within a campaign | Campaign error (`duplicate volume id`) |

A broken campaign file never aborts the tick: the error is contained to that
campaign and surfaced in `status.json` (and the campaign browser).

## Pipeline file — `pipelines/<id>.yaml`

```yaml
# Image MUST be digest-pinned — a mutable tag would let results published
# under one id be produced by different code over time.
image: 127.0.0.1:30500/htrflow-batch@sha256:34d462b8de7d…

steps:                     # htrflow pipeline steps, passed through verbatim
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
```

Rules enforced by `parse_pipeline` — a broken pipeline **raises** and blocks
submission for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is a DNS-1123 label (lowercase, `[a-z0-9.-]` interior, ≤63 chars) | It becomes the ConfigMap name `htr-pipeline-<id>` |
| `image:` contains `@sha256:` | Digest pin — provenance is recorded per volume in `manifest.json` |
| `steps:` is present | The steps are normalized (`yaml.safe_dump`) and hashed to `steps_sha256` |

## Immutability

A pipeline id is a **permanent name for a recipe**. Changing the steps or the
image under an existing id is drift; the reconciler detects it two ways
(see [How it Works → Campaigns](../how-it-works/campaigns.md)):

- **ConfigMap drift** — the live `htr-pipeline-<id>` ConfigMap's steps differ
  from git. Checked *before* the ConfigMap is re-applied, so the evidence is
  never overwritten.
- **Ground-truth drift** — a published `manifest.json` under the pipeline's
  prefix records a different `pipeline_sha256`. Pre-existing results from
  before the reconciler are grandfathered.

Either finding blocks the pipeline (nothing submits, no retry budgets are
spent) and lands as a warning in `status.json`. To change a recipe, mint a new
id (`demo-v2`) — the campaigns repo ships `scripts/check_immutable.sh` and a
GitHub Actions guard that refuses in-place edits to `pipelines/*.yaml`.
