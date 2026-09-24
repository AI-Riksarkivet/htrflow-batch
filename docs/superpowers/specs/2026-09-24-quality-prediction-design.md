# Quality prediction in campaigns — design

## Goal

A campaign can run an htrflow pipeline with a **QualityPrediction** step. The step scores each page's
transcription with a trained model. That model predicts a text-quality metric (bag-of-words F1 against a
ground truth) from features of the segmentation, layout, HTR confidence and text. The score is:

- **stored with the results**: the ALTO, `manifest.json`, the final `progress.json` and `iiif.json`;
- **shown in the campaign browser**: per volume, per campaign and per page;
- **shown in the viewer (UV4)**, next to the page's text.

Success means an operator can run a campaign with a QP step, sort its pages by predicted quality, and open
the worst ones in the viewer. Campaigns without a QP step behave exactly as they do today.

## Where the step comes from

The step exists today in two prototype repositories: `htrflow_qp`, a copy of htrflow with the step added, and
`quality-prediction`, the feature and model package (0.1.x, not on PyPI; its `modeling` extra brings
xgboost, scikit-learn, pandas and joblib). It is about to be merged into htrflow.

**Decision: wait for upstream; build everything else now.** The campaign image stays on its current htrflow
pin until the step is merged. Everything downstream of the step's output is built and tested now against a
**stand-in step** (see Testing). The image switch is the last task, and it is blocked on the upstream merge.

Three requests to the step's author, before it merges (sent separately):

1. Accept a Hugging Face Hub reference, `model_settings: {model, revision, model_file, bin_config_file}`,
   resolved with `hf_hub_download`, the same way htrflow's other model steps load.
2. Write the score into PAGE XML too. The ALTO template already writes `<Page PC=...>`.
3. Document `page_confidence` as the stable public key on the document.

If request 1 is accepted, the wrapper's resolution layer (section 3) is not built.

## 1. Image

- When the step is merged, the campaign image's htrflow pin moves to that upstream commit.
- `quality-prediction` and its model dependencies are added to the image. Prefer `xgboost-cpu` if it gives
  the same predictions, since scoring is CPU work and the full wheel is large. Measure the image size either
  way.
- **No model in the image.** The QP model is a Hub artifact, like every other model: fetched at warm-up onto
  the cache PVC and read offline by campaign pods.
- The image switch is proven by three runs:
  - test-driver-real with a real QP model;
  - the empty-output guard (`exportcheck.py`), which must still fail a flat pipeline;
  - a GPU run of the htr_demo pipelines on local k3s.

## 2. Pipeline format and converter validation

```yaml
- step: QualityPrediction
  settings:
    model_settings:
      model: <org>/<qp-model-repo>        # Hub repo id
      revision: <40-hex commit sha>
      model_file: <name>.joblib
      bin_config_file: <name>.json
    feature_groups: [segmentation, layout, htr_confidence, text]   # optional
```

The converter (`packages/converter/src/htrflow_converter/models.py`, beside `_flat_text`) refuses a pipeline
with a one-line reason when:

- `model_file` or `bin_config_file` is not a plain file name (it has a path separator, or is absolute or
  `..`);
- a local path is given where a Hub repo id is expected (the `.joblib` is a pickle, so loading it runs code;
  it must come from a pinned, reviewable source);
- `revision` is not a 40-hex commit sha;
- `feature_groups` names a group the `quality-prediction` package does not define;
- `feature_groups` names a group that needs a model the campaign image does not run (the DiT-based groups,
  for now);
- the step comes before the pipeline's text recognition step;
- the pipeline has more than one QualityPrediction step.

The QP settings are part of the recipe, so a change to them changes `recipe_sha256` and needs a new pipeline
id, like any other step change.

The chart's model-revision Kyverno policy already requires `settings.model_settings.revision` to be a 40-hex
sha for every step that has `settings.model_settings.model`, so QP steps are covered. Admission tests pin
that.

## 3. Wrapper: resolving the model

This section is built only if upstream keeps local file paths.

- **Warm-up (online):** for a QP step, `hf_hub_download(model, file, revision=...)` for both files into the
  recipe's model cache, next to the other models.
- **Campaign pod (offline, `HF_HUB_OFFLINE=1`):** the same calls resolve from the cache. The step's settings
  are rewritten to the prototype's constructor, `model=<path>` and `bin_config=<path>`, before htrflow loads
  the pipeline.
- **Failures:** a file missing from the cache, or a model that the installed `quality-prediction` version
  cannot load, is permanent: exit 13 (`EXIT_PERMANENT`) with a one-line reason. It is not retried.
- **Provenance:** the provenance stamp records the QP repo, revision and both file names.

## 4. Storage

Without a QP step, none of this is written and every file is byte-identical to today.

| File | What is added |
| --- | --- |
| ALTO | `<Page PC="0.87">`, written by htrflow's template |
| PAGE XML | whatever upstream settles on (request 2) |
| `manifest.json` | per page: `"quality": 0.87`; top level: `"quality": {target, model, revision, mean, min, scored, lowest}` |
| final `progress.json` | the same top-level summary, so the web needs no extra reads |
| `iiif.json` | per canvas: metadata "Predicted quality: 0.87"; manifest-level metadata with the volume summary |

- `target` is the predicted metric (`bow_f1`).
- `scored` is how many pages have a score.
- `lowest` is a short list of the worst pages, by page index.
- The source is `document.annotations["page_confidence"]`, clamped to 0–1.

The writers are `publish.py` (`manifest.json`, final `iiif.json`), `progress.py` (`progress.json`, interim
`iiif.json`) and `viewer.py` (canvas metadata).

## 5. Campaign browser

- **Volume row:** a fixed-width "quality" column showing the volume mean. Empty when the volume has no
  score, and the row does not shift while it loads.
- **Totals row:** the campaign mean, marked `≥`/partial while some volumes are still unscored, plus
  "lowest pages" links that open the page in the viewer.
- **Pages table (`PagesTable.svelte`):** a sortable quality column.
- **Folded card:** unchanged.
- **No colour thresholds.** What counts as "bad" depends on the material. It is not decided in the UI.
- **API:** the web passes the fields through from `progress.json`/`manifest.json`
  (`packages/web/src/htrflow_web/projection.py`). The API contract fixture and the frontend's Zod schema
  gain the optional fields.

## 6. Viewer (UV4 patch)

The patch is an extension of `.docker/uv4-uv-html.patch`, applied to the pinned Riksarkivet `universalviewer4`
commit in the web image build.

- **Text panel** (`TextRightPanel.ts`): shows "Predicted quality 0.87" in its header, read from `<Page PC>`
  of the ALTO it has already fetched. That way the score always belongs to the text shown under it. Without
  `PC`, nothing is shown. The value is inserted as text, never as HTML.
- **"More information" panel:** no code change. It already lists the canvas and manifest metadata from
  section 4. The patch only makes sure the panel config does not hide canvas metadata.
- **Not in scope:** thumbnail badges and sorting canvases by quality. The browser's pages table covers
  "where are the bad pages?".

## 7. Testing

**Built now, with a stand-in QP step.** This is a tiny htrflow step in the test image that writes a fixed
`page_confidence`. It proves everything downstream of that key:

- **Converter:** a table test per validation rule in section 2, and a test that the recipe hash changes when
  the QP settings change.
- **Admission:** Kyverno dry-run tests that the model-revision policy refuses an unpinned QP step and admits
  a pinned one.
- **Wrapper:** Hub resolution against a local fake Hub cache:
  - resolves offline;
  - a missing file exits 13;
  - a version mismatch exits 13;
  - the provenance stamp includes the QP fields.
- **Storage:** a golden test on a two-page run checks per-page and summary fields in `manifest.json`, the
  summary in the final `progress.json`, and canvas metadata in `iiif.json`. The same run without a QP step
  gives byte-identical outputs to today.
- **Web/API:** the contract fixture and schema are updated. Frontend tests for:
  - the quality column;
  - the sortable pages table;
  - the partial `≥` marker;
  - the "lowest pages" links;
  - no layout shift (the CLS measurement script).
- **UV4:** the patched text panel reads `PC` and inserts it as text, and shows nothing without it. The web
  image build fails if the patch does not apply.

**At the image switch:** the three runs in section 1. If upstream changes the interface (Hub reference or
key name), only the resolution layer and the stand-in step change; the tests around them stay.

## Out of scope

- Colour thresholds or automatic re-runs of low-scoring pages.
- The DiT feature groups.
- Training or evaluating QP models. This design runs a model; it does not make one.
- Quality badges in the viewer's thumbnail strip.
