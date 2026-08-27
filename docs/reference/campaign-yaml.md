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

  # 2) Explicit IIIF manifest (Presentation v2 or v3), http(s) only:
  - id: loc-mal2459400
    manifest: https://www.loc.gov/item/mal2459400/manifest.json

  # 3) Bare image URLs (http(s) only) — the reconciler builds and publishes
  #    a synthetic IIIF manifest for them (see S3 Layout: sources/):
  - id: htr-demo-examples
    images:
      - https://example.org/page1.jpg
      - https://example.org/page2.jpg
```

Rules enforced by `parse_campaign`:

| Rule | Consequence when violated |
|------|---------------------------|
| `pipeline:` is required and must name a file in `pipelines/` | Campaign is reported with an error in `status.json`; nothing submits |
| Every volume needs `manifest:` or a non-empty `images:` (unless it is a bare string) | Campaign error |
| `manifest:` and every `images:` entry are absolute `http://` or `https://` URLs | Campaign error (`must be an http(s) URL`) — nothing else ever reaches the pre-validation fetch, the wrapper or the browser |
| Volume ids match `[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?` — alphanumeric at both ends, ≤63 chars | Campaign error (`unsafe volume id`). This is the Kubernetes **label-value** alphabet, not a DNS-1123 label: uppercase is allowed, and the id is lowercased where it becomes a Job name or label |
| Volume ids are unique within a campaign | Campaign error (`duplicate volume id`) |

A broken campaign file never aborts the tick: the error is contained to that
campaign and surfaced in `status.json` (and the campaign browser). The ids a
broken file *does* name still count for orphan accounting, so a typo cannot
make every result of that pipeline look orphaned.

The campaign file stem becomes the Job label `batch.htrflow/campaign`
(label-sanitised), which the reconciler's fairness order counts.

## Pipeline file — `pipelines/<id>.yaml`

```yaml
# Image MUST be digest-pinned — a mutable tag would let results published
# under one id be produced by different code over time.
image: ghcr.io/riksarkivet/htrflow-batch@sha256:34d462b8de7d…

steps:                     # htrflow pipeline steps, passed through verbatim
  - step: Segmentation
    settings:
      model: yolo
      model_settings:
        model: Riksarkivet/yolov9-regions-1
        revision: 0123456789abcdef0123456789abcdef01234567   # required when
                                                             # security.requireModelRevision
  - step: TextRecognition
    settings:
      model: TrOCR
      model_settings:
        model: Riksarkivet/trocr-base-handwritten-hist-swe-2
        revision: …
```

Rules enforced by `parse_pipeline` — a broken pipeline **raises** and blocks
submission for every campaign that uses it:

| Rule | Why |
|------|-----|
| Pipeline id is a DNS-1123 label (lowercase, `[a-z0-9.-]` interior, ≤63 chars) | It becomes the ConfigMap name `htr-pipeline-<id>` |
| `image:` contains `@sha256:` | Digest pin — provenance is recorded per volume in `manifest.json` |
| When `security.allowedImageRepos` is set: the repository (the part before `@`) equals one entry or starts with `<entry>/` | The campaigns repo is a code-execution boundary; the allow-list is what keeps it to images you built. Empty = any registry, with a warning in `status.json` |
| When `security.requireModelRevision` is true: every `model_settings.model` carries a 40-hex `revision:` | HF Hub weights are pickles and an unpinned repo is mutable |
| `steps:` is present | Only the `steps:` document goes into the ConfigMap; no `Export` steps (the wrapper appends them — a pipeline with one fails the warm-up) |

## Immutability

A pipeline id is a **permanent name for a recipe**. Changing the steps or the
image under an existing id is drift; the reconciler detects it two ways
(see [How it Works → Campaigns](../how-it-works/campaigns.md#immutability-and-the-drift-guards)):

- **ConfigMap drift** — the live `htr-pipeline-<id>` ConfigMap's steps differ
  from git, compared as parsed content (so a PyYAML re-flow is not drift).
  Checked *before* the ConfigMap is re-applied, so the evidence is never
  overwritten.
- **Ground-truth drift** — one published `manifest.json` under the
  pipeline's prefix records a different recipe. Two hashes are accepted for
  the steps: the canonical-JSON sha256 of the parsed `steps` (sorted keys,
  no whitespace — `parse.canonical_sha256`) and the legacy sha256 of the
  ConfigMap text, which is what the wrapper still publishes as
  `pipeline_sha256`. A different `image_digest` is drift too; results that
  predate image pinning (`image_digest: "unknown"`) and manifests with no
  `pipeline_sha256` at all are grandfathered with a warning instead of
  blocking forever.

Either finding blocks the pipeline (nothing submits, no retry budgets are
spent) and lands as a warning in `status.json`. To change a recipe, mint a new
id (`demo-v2`) — the PoC's campaigns repo carries `scripts/check_immutable.sh`
and a GitHub Actions guard (`guard.yml`) that refuses in-place edits to
`pipelines/*.yaml` on pull requests; it only helps with a protected `main`.
