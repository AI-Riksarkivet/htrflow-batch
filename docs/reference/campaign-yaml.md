# Campaign & Pipeline YAML

The campaigns git repo has three files/directories the converter reads:
`converter.yaml` (cluster-wide defaults), `campaigns/*.yaml` (what to run)
and `pipelines/*.yaml` (how to run it). The filename stem is the campaign /
pipeline id.

Source: [`packages/converter/src/htrflow_converter/parse.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/parse.py),
[`models.py`](https://github.com/AI-Riksarkivet/htrflow-batch/blob/main/packages/converter/src/htrflow_converter/models.py).

## `converter.yaml`

Cluster-wide defaults for everything the converter renders. Unknown keys are
rejected. All fields are optional — the values below are the defaults.

```yaml title="converter.yaml"
namespace: htr-batch              # Kubernetes namespace campaigns render into
queue: htr-batch                  # Kueue LocalQueue name
window: 20                        # Job parallelism, and the CAP a campaign's own `window:` is clamped to
s3_secret: htr-batch-s3           # Secret carrying S3 credentials
data_pvc: htr-test-data           # PVC mounted as the model cache
runtime_class: nvidia             # RuntimeClass for GPU pods
node_selector: {}
tolerations: []
public_results_base: ""           # public URL prefix results are served from (required for the read API)
legacy_layout: false              # true: <pipeline>/<volume>/ layout, no per-namespace prefix
source_template: "https://lbiiif.riksarkivet.se/arkis!{ref}/manifest"
max_seconds: 21600                # MAX_SECONDS passed to the wrapper; a pipeline's own `max_seconds:` overrides it
manifest_max_bytes: 16777216      # 16 MiB
fetch_max_bytes: 67108864         # 64 MiB
allowed_image_repos: []           # empty = any registry; see Security → Trust boundary
require_model_revision: false
```

## Campaign file — `campaigns/<name>.yaml`

```yaml
pipeline: demo-v1          # required: a pipeline id from pipelines/
priority: ""                # optional: a Kueue PriorityClass name
window: 20                   # optional: this campaign's parallelism, clamped to converter.yaml's window
volumes:
  # 1) Bare string: a Riksarkivet reference code. The manifest URL is
  #    templated from converter.yaml's source_template.
  - R0001203

  # 2) Explicit IIIF manifest (Presentation v2 or v3), http(s) only:
  - id: loc-mal2459400
    manifest: https://www.loc.gov/item/mal2459400/manifest.json

  # 3) Bare image URLs (http(s) only) — the wrapper builds and publishes
  #    a synthetic IIIF manifest for them itself (see S3 Layout: sources/):
  - id: htr-demo-examples
    images:
      - https://example.org/page1.jpg
      - https://example.org/page2.jpg
```

Rules enforced by `parse_campaign` (`validate`, and by `render`):

| Rule | Consequence when violated |
|------|---------------------------|
| `pipeline:` is required and must name a file in `pipelines/` | Reported as a validation error; nothing renders |
| Every volume needs `manifest:` or a non-empty `images:` (unless it is a bare string) | Validation error |
| `manifest:` and every `images:` entry are absolute `http://` or `https://` URLs | Validation error (`must be an http(s) URL`) |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` — alphanumeric at both ends, ≤63 chars | Validation error (`unsafe volume id`). This is the Kubernetes **label-value** alphabet, not a DNS-1123 label: uppercase is allowed |
| Volume ids are unique within a campaign | Validation error (`duplicate volume id`) |
| `window:`, when set, is a positive integer | Validation error |
| `window:` above `converter.yaml`'s `window` | Silently clamped to it at render time — `converter.yaml`'s value is the per-cluster cap and should be set to what the ClusterQueue's GPU quota can actually admit. Rendering more and letting Kueue's partial admission shrink it on the live Job is what this replaced: Kueue then rewrites `spec.parallelism` and rejects every later apply of the unchanged rendered file (`cannot change when partial admission is enabled and the job is not suspended`) |
| **A campaign whose rendered Job already exists in `rendered/` with a different volume list is rejected** | `render` prints `campaign <name> is append-only: create a new campaign` and exits non-zero — Job `completions` is immutable once created, so adding volumes means a new campaign file |

The campaign file stem becomes the Job name and the `htrflow.riksarkivet.se/campaign`
label.

## Pipeline file — `pipelines/<id>.yaml`

```yaml
# Image MUST be digest-pinned — a mutable tag would let results published
# under one id be produced by different code over time.
image: ghcr.io/riksarkivet/htrflow-batch@sha256:34d462b8de7d…

max_seconds: 3600          # optional: this recipe's per-volume wall-clock
                           # budget (MAX_SECONDS), overriding converter.yaml's

steps:                     # htrflow pipeline steps, passed through verbatim
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
        revision: 0123456789abcdef0123456789abcdef01234567   # required when
                                                             # require_model_revision
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        revision: …
```

Rules enforced by `parse_pipeline` — a broken pipeline is reported as a
validation error and blocks rendering for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is a DNS-1123 label (lowercase, `[a-z0-9.-]` interior, ≤63 chars) | It becomes the ConfigMap name `htr-pipeline-<id>` |
| `image:` contains `@sha256:` | Digest pin — provenance is recorded per volume in `manifest.json` |
| When `converter.yaml`'s `allowed_image_repos` is set: the repository (the part before `@`) equals one entry or starts with `<entry>/` | The campaigns repo is a code-execution boundary; the allow-list is what keeps it to images you built |
| When `converter.yaml`'s `require_model_revision` is true: every `model_settings.model` carries a 40-hex `revision:` | HF Hub weights are pickles and an unpinned repo is mutable |
| `max_seconds:`, when set, is a positive integer | It becomes the Job's `MAX_SECONDS` env for every campaign on this pipeline; unset falls back to `converter.yaml`. A sixty-page spread recipe and a single-page one do not want the same budget, and a budget the volume cannot meet costs `backoffLimitPerIndex` retries before the index is capped |
| `steps:` is present | Only the `steps:` document goes into the ConfigMap; no `Export` steps (the wrapper appends them — a pipeline with one fails the warm-up) |

## `rendered/`

`htrflow-campaigns render <repo-dir> --out <repo-dir>/rendered` writes:

```
rendered/
  pipelines/<id>.yaml     # ConfigMap htr-pipeline-<id> + Job htr-warmup-<id>
  campaigns/<name>.yaml   # ConfigMap campaign-<name> + the campaign's Indexed Job
```

This directory is generated, committed by the campaigns repo's own CI on
`main` (never hand-edited), and is what Argo CD (or `make campaigns-apply`
on the PoC) actually applies — `kubectl apply -f rendered/pipelines -f
rendered/campaigns` (pipelines first, since a campaign's Job references its
pipeline's ConfigMap).

`render` also **removes** files under `--out` that this render did not
produce, so deleting `campaigns/<name>.yaml` deletes
`rendered/campaigns/<name>.yaml` too. Deleting the manifest is only half of
cancelling: the apply has to prune as well (Argo CD does; `make
campaigns-apply PRUNE=1` passes `kubectl apply --prune -l
htrflow.riksarkivet.se/managed-by=converter`). Every object the converter
renders — both ConfigMaps and both Jobs — carries that label for exactly
this reason.

## Immutability

A pipeline id is a **permanent name for a recipe**: changing the steps or the
image under an existing id is drift once results exist under it. There is no
runtime drift guard any more — the converter is a pure function and renders
whatever is in git — so this is enforced by review convention, not code:
protect the campaigns repo's `main` and require review, the same as for CI
config. To change a recipe, mint a new id (`demo-v2`); old results under
`demo-v1` stay untouched and comparable side by side.

A campaign, separately, is append-only at the volume-list level (see the
table above) — that one *is* enforced by `render`, because a running Job's
`completions` genuinely cannot change.
