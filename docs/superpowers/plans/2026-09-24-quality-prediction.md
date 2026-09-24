# Quality Prediction in Campaigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a campaign's pipeline a QualityPrediction step, and carry each page's predicted quality from
its ALTO into `manifest.json`, `progress.json`, `iiif.json`, the campaign browser and the viewer. All of it
is built and tested before htrflow ships the step.

**Architecture:** htrflow's QualityPrediction step writes each page's score to ALTO as `<Page PC="…">`. The
wrapper already parses every ALTO it uploads (for page sizes). It reads `PC` at the same point, and when it
reads back a resumed page's ALTO. That gives one number per page, taken from the same file the viewer
shows. `publish` turns those numbers into per-page and volume fields. The read API passes the volume
summary through and sums it per campaign. The frontend draws a column. The UV4 text panel reads `PC` from
the ALTO it already loads.

**Tech Stack:** Python 3.12 (pydantic, pytest, moto), Kyverno CLI admission tests, SvelteKit + Zod +
vitest (bun), the UV4 patch (TypeScript), Docker.

**Spec:** `docs/superpowers/specs/2026-09-24-quality-prediction-design.md`

**Where this plan refines the spec** (the spec travels with it; these three points win):
1. **The wrapper reads ALTO `Page/@PC`**, not `document.annotations["page_confidence"]`. `PC` is what
   htrflow writes from that key. Reading the file means a resumed page's score is read back like its size,
   and the numbers can never disagree with the file a reader opens.
   The spec's "stand-in step" is therefore **ALTO fixtures that carry `PC`**. No fake htrflow step is
   needed until the image switch.
2. **A partial campaign mean** is said the way the page totals already say it: "scored in X of Y volumes".
   It is not a `≥` glyph, since a mean over some volumes is not a lower bound.
3. **The wrapper's Hub-resolution layer** (spec §3), **the provenance fields** and **the image switch**
   (spec §1) are **held**. They depend on what the merged upstream step takes, so they are listed at the end
   and not executed now.

## Global Constraints

- Nothing changes for a run without scores. With no page whose ALTO carries a valid `PC`:
  - no `quality` key anywhere in `manifest.json`, `progress.json` or `iiif.json`;
  - no `metadata` added to `iiif.json`;
  - the read API sends `"quality": null`.
- The page score is **clamped to nothing**. A `PC` that is not a finite number in [0, 1] is treated as no
  score (ALTO's `PCType` is 0–1).
- Scores are rounded to **4 decimals** in stored files and shown with **2 decimals** in the UI and viewer.
- `lowest` holds at most **5** pages, ascending by score, ties broken by page name.
- `target` is the string `"bow_f1"`.
- **No colour thresholds** anywhere in the UI.
- The QP model is a Hub artifact with a **40-hex `revision`**. **No model in any image.**
- Feature groups the campaign image can run: `segmentation`, `layout`, `htr_confidence`, `text`.
  Groups the `quality-prediction` package knows but that need more than htrflow's tree:
  `image`, `dit`, `regionization`, `ngram`, `lm`, `lexicon`, `interaction`, `metadata`.
- Repo rules:
  - no people's names, dates, versions, hosts or hardware in site docs (`docs/` minus `docs/superpowers`);
    `scripts/docs-site.sh build --clean --strict` runs `docs_lint` and must pass;
  - one logical step per commit;
  - no `Co-Authored-By` trailers;
  - before any push: `make ci`, plus `make frontend-test frontend-check` for frontend tasks.

## Review Focus

1. **`PC` present but not a number** (`PC=""`, `PC="nan"`, `PC="1.5"`, hand-edited ALTO). Expected: that
   page has no score and the page still publishes. Pinned in Task 3.
2. **A resumed volume.** Pages done by an earlier attempt must keep their scores in the final
   `manifest.json`. Expected: the summary counts every scored page, resumed or not. Pinned in Task 4.
3. **A volume where only some pages are scored** (a QP failure on one page, or a pipeline edit). Expected:
   `scored` < `pages`, the mean covers only scored pages, and unscored pages have no `quality` key. Pinned
   in Task 4.
4. **A hostile or half-written `progress.json`**. Its `quality` could be a string, have 10 000 `lowest`
   entries, or have `mean: 7`. Expected: the API drops it to `null` or clips it, and never passes it
   through. Pinned in Task 6.
5. **A campaign whose QP step exists but no volume has finished.** Expected: the quality column is there
   (so nothing shifts when numbers arrive), its cells are empty, and there is no "lowest" line. Pinned in
   Task 8.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `packages/converter/src/htrflow_converter/models.py` | Refuse a malformed QualityPrediction step (new `_quality_step_problem`) |
| `packages/converter/tests/test_models.py` | Its table tests |
| `packages/converter/tests/test_policy_admission.py` | Model-revision policy covers a QP step |
| `packages/wrapper/src/htrflow_batch/quality.py` (new) | `parse_alto_quality`, `qp_model`, `summary`: the only place that knows the score's shape |
| `packages/wrapper/src/htrflow_batch/store.py` | `page_quality` dict filled on upload |
| `packages/wrapper/src/htrflow_batch/publish.py` | Read back resumed pages' scores; per-page + summary in `manifest.json`; returns `Published` |
| `packages/wrapper/src/htrflow_batch/progress.py` | `quality` in the final `progress.json`; interim canvases get scores |
| `packages/wrapper/src/htrflow_batch/viewer.py` | Canvas + manifest `metadata` |
| `packages/wrapper/src/htrflow_batch/main.py` | Wire `Published.quality` into the tracker |
| `scripts/wrapper_contract.py` → `frontend/src/lib/fixtures/wrapper-contract.json` | A scored manifest in the contract |
| `packages/web/src/htrflow_web/progress.py` | `_quality` sanitiser on both readers |
| `packages/web/src/htrflow_web/projection.py` | `_campaign_quality` beside `_campaign_pages` |
| `scripts/api_contract.py` → `frontend/src/lib/fixtures/api-contract.json` | A scored campaign in the contract |
| `frontend/src/lib/api.ts`, `frontend/src/lib/run.ts` | Zod schemas |
| `frontend/src/lib/quality.ts` (new) | `formatQuality`, `hasQualityStep`, `sortByQuality`: pure, tested |
| `frontend/src/lib/components/PagesTable.svelte` | Sortable quality column |
| `frontend/src/lib/components/CampaignCard.svelte` | Quality track, totals cell, lowest-pages line |
| `.docker/uv4-uv-html.patch` | Text panel shows `PC` |
| `packages/web/tests/test_uv4_patch.py` (new) | Pins the patch's quality hunk |
| `docs/reference/campaign-yaml.md`, `docs/reference/s3-layout.md`, `docs/reference/web.md` | Operator-facing reference |

---

### Task 1: Converter refuses a malformed QualityPrediction step

**Files:**
- Modify: `packages/converter/src/htrflow_converter/models.py`. Add the constants and function beside
  `_flat_text` (around line 645), and a validator on `Pipeline` after `_check_text_reaches_export`
  (around line 787).
- Test: `packages/converter/tests/test_models.py`
- Docs: `docs/reference/campaign-yaml.md`, the "Pipeline file" section

**Interfaces:**
- Produces: `models._quality_step_problem(steps: list) -> str | None`, following the same convention as
  `_flat_text`: the refusal sentence, or `None`.

- [ ] **Step 1: Write the failing tests** (append to `test_models.py`)

```python
_SHA = "0123456789abcdef0123456789abcdef01234567"
_IMAGE = "ghcr.io/x/y@sha256:" + "a" * 64
_SEG = {"step": "Segmentation", "settings": {"model": "yolo", "model_settings": {"model": "o/r"}}}
_LINES = {"step": "Segmentation", "settings": {"model": "yolo", "model_settings": {"model": "o/l"}}}
_HTR = {"step": "TextRecognition", "settings": {"model": "TrOCR", "model_settings": {"model": "o/t"}}}


def _qp(**model_settings) -> dict:
    ms = {
        "model": "org/qp-model",
        "revision": _SHA,
        "model_file": "model.joblib",
        "bin_config_file": "bins.json",
        **model_settings,
    }
    return {"step": "QualityPrediction", "settings": {"model_settings": ms}}


def _pipeline(*steps) -> Pipeline:
    return Pipeline.model_validate({"id": "p", "image": _IMAGE, "steps": list(steps)})


def _refusal(*steps) -> str:
    with pytest.raises(ValidationError) as exc_info:
        _pipeline(*steps)
    return " ".join(str(e["msg"]) for e in exc_info.value.errors())


def test_a_quality_prediction_step_after_recognition_is_accepted():
    p = _pipeline(_SEG, _LINES, _HTR, _qp())
    assert p.steps[-1]["step"] == "QualityPrediction"


def test_the_json_feature_groups_are_accepted():
    qp = _qp()
    qp["settings"]["feature_groups"] = ["segmentation", "layout", "htr_confidence", "text"]
    _pipeline(_SEG, _LINES, _HTR, qp)


@pytest.mark.parametrize(
    "change,words",
    [
        ({"model": "/models/qp.joblib"}, "Hugging Face Hub repo id"),
        ({"model": "./qp"}, "Hugging Face Hub repo id"),
        ({"model": "qp-model"}, "Hugging Face Hub repo id"),
        ({"revision": "main"}, "40-hex"),
        ({"revision": None}, "40-hex"),
        ({"model_file": "sub/model.joblib"}, "plain file name"),
        ({"model_file": "/abs/model.joblib"}, "plain file name"),
        ({"bin_config_file": ".."}, "plain file name"),
        ({"model_file": ""}, "plain file name"),
    ],
    ids=["abs-path", "rel-path", "no-org", "branch", "no-rev", "subdir", "abs-file", "dotdot", "empty"],
)
def test_a_quality_model_not_pinned_on_the_hub_is_refused(change, words):
    message = _refusal(_SEG, _LINES, _HTR, _qp(**change))
    assert "QualityPrediction" in message and words in message


def test_an_unknown_feature_group_is_refused_and_named():
    qp = _qp()
    qp["settings"]["feature_groups"] = ["layout", "vibes"]
    message = _refusal(_SEG, _LINES, _HTR, qp)
    assert "vibes" in message and "segmentation" in message


def test_a_feature_group_the_image_cannot_run_is_refused_as_such():
    qp = _qp()
    qp["settings"]["feature_groups"] = ["dit"]
    message = _refusal(_SEG, _LINES, _HTR, qp)
    assert "dit" in message and "cannot run" in message


def test_quality_prediction_before_recognition_is_refused():
    message = _refusal(_SEG, _LINES, _qp(), _HTR)
    assert "after the TextRecognition step" in message


def test_quality_prediction_with_no_recognition_is_refused():
    message = _refusal(_SEG, _LINES, _qp())
    assert "after the TextRecognition step" in message


def test_two_quality_prediction_steps_are_refused():
    message = _refusal(_SEG, _LINES, _HTR, _qp(), _qp())
    assert "one QualityPrediction step" in message


def test_quality_prediction_settings_move_the_recipe():
    a = _pipeline(_SEG, _LINES, _HTR, _qp())
    b = _pipeline(_SEG, _LINES, _HTR, _qp(revision="f" * 40))
    assert a.recipe_sha256 != b.recipe_sha256
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/converter/tests/test_models.py -k "quality" -v`
Expected: the refusal tests FAIL with `DID NOT RAISE`. The accept tests and the recipe test already PASS.
That is expected: the recipe already hashes every step.

- [ ] **Step 3: Implement** (in `models.py`, after `_flat_text`)

```python
#: htrflow's QualityPrediction step (htrflow_qp, to be merged into htrflow):
#: an XGBoost model scores the page from features of the tree htrflow built.
#: Its model is a joblib pickle -- loading it runs code -- so it comes from a
#: Hub repo at a commit, like every other model here, never from a path.
_QP_STEP = "qualityprediction"
_HUB_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
#: The groups quality_prediction.inference reads off htrflow's tree
#: (JSON_FEATURE_GROUPS) -- all this image can compute.
_QP_GROUPS = ("segmentation", "layout", "htr_confidence", "text")
#: The rest of the package's PageFeatureExtractor.FEATURE_GROUPS: they need
#: the page image, a DiT model or a language model this image does not run.
_QP_GROUPS_NOT_RUN = frozenset(
    {"image", "dit", "regionization", "ngram", "lm", "lexicon", "interaction", "metadata"}
)
_TEXT_RECOGNITION = "textrecognition"


def _plain_file(value: object) -> bool:
    return (
        isinstance(value, str)
        and value not in ("", ".", "..")
        and "/" not in value
        and "\\" not in value
    )


def _quality_step_problem(steps: list) -> str | None:
    """The sentence refusing a QualityPrediction step this system cannot run
    as written; ``None`` when there is none or it is fine."""
    names = [str(s.get("step", "")).lower() if isinstance(s, dict) else "" for s in steps]
    at = [i for i, n in enumerate(names) if n == _QP_STEP]
    if not at:
        return None
    if len(at) > 1:
        return (
            f"has {len(at)} QualityPrediction steps (steps "
            f"{', '.join(str(i + 1) for i in at)}) — a page gets one predicted "
            "quality; keep one QualityPrediction step"
        )
    i = at[0]
    if _TEXT_RECOGNITION not in names[:i]:
        return (
            f"runs QualityPrediction (step {i + 1}) before any text is read — "
            "it scores the transcription, so put it after the TextRecognition step"
        )
    settings = steps[i].get("settings")
    ms = settings.get("model_settings") if isinstance(settings, dict) else None
    ms = ms if isinstance(ms, dict) else {}
    where = f"QualityPrediction (step {i + 1})"
    if not isinstance(ms.get("model"), str) or not _HUB_REPO_RE.match(ms["model"]):
        return (
            f"{where}: model_settings.model must be a Hugging Face Hub repo id "
            f"(<org>/<repo>), got {shown(ms.get('model'))} — the model is a "
            "pickle, so it is loaded from a pinned Hub commit, never a path"
        )
    if not isinstance(ms.get("revision"), str) or not _COMMIT_RE.match(ms["revision"]):
        return (
            f"{where}: model_settings.revision must be the 40-hex commit of "
            f"{ms['model']}, got {shown(ms.get('revision'))}"
        )
    for key in ("model_file", "bin_config_file"):
        if not _plain_file(ms.get(key)):
            return (
                f"{where}: model_settings.{key} must be a plain file name in "
                f"{ms['model']}, got {shown(ms.get(key))}"
            )
    groups = settings.get("feature_groups") if isinstance(settings, dict) else None
    if groups is not None:
        if not isinstance(groups, list) or not groups:
            return f"{where}: feature_groups must be a non-empty list of group names"
        not_run = [g for g in groups if g in _QP_GROUPS_NOT_RUN]
        unknown = [g for g in groups if g not in _QP_GROUPS and g not in _QP_GROUPS_NOT_RUN]
        if unknown:
            return (
                f"{where}: unknown feature group {', '.join(map(str, unknown))} — "
                f"use {', '.join(_QP_GROUPS)}"
            )
        if not_run:
            return (
                f"{where}: this image cannot run feature group "
                f"{', '.join(not_run)} — it computes {', '.join(_QP_GROUPS)} only"
            )
    return None
```

Then add the validator to `Pipeline`, directly after `_check_text_reaches_export`:

```python
    @field_validator("steps")
    @classmethod
    def _check_quality_step(cls, v: list[dict], info: ValidationInfo) -> list:
        if not _as_recorded(info) and (why := _quality_step_problem(v)):
            raise ValueError(why)
        return v
```

Check that `re` is already imported at the top of `models.py` (it is: `_NAME_RE`). `shown` is defined at
line 132.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --no-sync pytest packages/converter/tests/test_models.py -v`
Expected: all PASS.

- [ ] **Step 5: Document the step** in `docs/reference/campaign-yaml.md`. Add a `### Predicted page quality`
  subsection under "Pipeline file", after "Regions, then lines". Include the YAML block from spec §2 and
  one sentence per rule above. It must name no model repo, date or version.

Run: `scripts/docs-site.sh build --clean --strict` (with `ZENSICAL=.venv/bin/zensical` after
`uv sync --locked --only-group docs`). Expected: `No issues found`.

- [ ] **Step 6: Commit** (two commits)

```bash
git add packages/converter/src/htrflow_converter/models.py packages/converter/tests/test_models.py
git commit -m "feat(converter): refuse a QualityPrediction step that is not a pinned Hub model after recognition"
git add docs/reference/campaign-yaml.md
git commit -m "docs(reference): the QualityPrediction step and what the converter checks"
```

---

### Task 2: Admission covers a QP step

**Files:**
- Test: `packages/converter/tests/test_policy_admission.py`

**Interfaces:**
- Consumes: the existing `render_policy`, `admission` and `pipeline` helpers in that file, and its
  `REVISION`, `YOLO` and `TROCR` constants.

- [ ] **Step 1: Write the tests** (append)

```python
QP = {
    "step": "QualityPrediction",
    "settings": {
        "model_settings": {
            "model": "org/qp-model",
            "revision": REVISION,
            "model_file": "model.joblib",
            "bin_config_file": "bins.json",
        },
        "feature_groups": ["segmentation", "layout", "htr_confidence", "text"],
    },
}


def test_a_pinned_quality_prediction_step_is_admitted(tmp_path: Path):
    policy = render_policy(tmp_path, "model-revision")
    verdict, out = admission(tmp_path, policy, pipeline(YOLO, TROCR, QP))
    assert verdict == "admitted", out


def test_an_unpinned_quality_prediction_model_is_refused(tmp_path: Path):
    """Its model is a pickle: the same rule as every other model, and the
    policy needs no change to apply it -- it reads any step with
    settings.model_settings.model."""
    policy = render_policy(tmp_path, "model-revision")
    unpinned = yaml.safe_load(yaml.safe_dump(QP))
    del unpinned["settings"]["model_settings"]["revision"]
    verdict, out = admission(tmp_path, policy, pipeline(YOLO, TROCR, unpinned))
    assert verdict == "refused", out
    assert "org/qp-model" in out
```

- [ ] **Step 2: Run them**

Run: `PATH="$HOME/.local/bin:$PATH" uv run --no-sync pytest packages/converter/tests/test_policy_admission.py -k quality -v`
(the `kyverno` CLI must be on PATH, as for the rest of this file).
Expected: both PASS with no policy change. If the refusal test fails, stop and report. The spec assumes
the policy already covers this shape, and a fix to the policy is a design change.

- [ ] **Step 3: Commit**

```bash
git add packages/converter/tests/test_policy_admission.py
git commit -m "test(chart): the model-revision policy pins a QualityPrediction model too"
```

---

### Task 3: The wrapper reads each page's score off its ALTO

**Files:**
- Create: `packages/wrapper/src/htrflow_batch/quality.py`
- Modify: `packages/wrapper/src/htrflow_batch/store.py`. Add `__init__` state around line 88, and the
  end of `upload_page` around line 199.
- Test: `packages/wrapper/tests/test_quality.py` (new), `packages/wrapper/tests/test_store.py`

**Interfaces:**
- Produces:
  - `quality.parse_alto_quality(root: ET.Element) -> float | None`
  - `quality.qp_model(pipeline_text: str) -> tuple[str | None, str | None]` (repo, revision)
  - `quality.summary(scores: Mapping[str, float], pipeline_text: str, canvases: Sequence[str]) -> dict | None`
  - `quality.TARGET = "bow_f1"`, `quality.LOWEST = 5`, `quality.DIGITS = 4`
  - `ResultStore.page_quality: dict[str, float]`

- [ ] **Step 1: Write the failing tests** (`packages/wrapper/tests/test_quality.py`)

```python
import xml.etree.ElementTree as ET

import pytest

from htrflow_batch import quality

NS = "http://www.loc.gov/standards/alto/ns-v4#"


def _alto(pc: str | None, ns: bool = True) -> ET.Element:
    attr = "" if pc is None else f' PC="{pc}"'
    xmlns = f' xmlns="{NS}"' if ns else ""
    return ET.fromstring(
        f'<alto{xmlns}><Layout><Page WIDTH="10" HEIGHT="10"{attr}/></Layout></alto>'
    )


@pytest.mark.parametrize("ns", [True, False])
def test_the_score_is_page_pc(ns):
    assert quality.parse_alto_quality(_alto("0.8731", ns)) == pytest.approx(0.8731)


@pytest.mark.parametrize("pc", [None, "", "nan", "inf", "-0.1", "1.5", "high"])
def test_anything_but_a_number_in_0_1_is_no_score(pc):
    assert quality.parse_alto_quality(_alto(pc)) is None


def test_the_edges_are_scores():
    assert quality.parse_alto_quality(_alto("0")) == 0.0
    assert quality.parse_alto_quality(_alto("1")) == 1.0


PIPELINE = """steps:
- step: TextRecognition
  settings: {model: TrOCR, model_settings: {model: o/t}}
- step: QualityPrediction
  settings:
    model_settings:
      model: org/qp-model
      revision: 0123456789abcdef0123456789abcdef01234567
      model_file: model.joblib
      bin_config_file: bins.json
"""


def test_the_model_is_read_off_the_pipeline():
    assert quality.qp_model(PIPELINE) == (
        "org/qp-model",
        "0123456789abcdef0123456789abcdef01234567",
    )


@pytest.mark.parametrize("text", ["steps: []\n", "not: [valid", "", "steps: 3\n"])
def test_a_pipeline_without_the_step_names_no_model(text):
    assert quality.qp_model(text) == (None, None)


def test_no_scores_is_no_summary():
    assert quality.summary({}, PIPELINE, ["0001"]) is None


def test_the_summary_field_for_field():
    scores = {"0001": 0.9, "0002": 0.41234567, "0003": 0.7, "0004": 0.41234567}
    body = quality.summary(scores, PIPELINE, ["0001", "0002", "0004"])
    assert body == {
        "target": "bow_f1",
        "model": "org/qp-model",
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "mean": 0.6062,
        "min": 0.4123,
        "scored": 4,
        # ascending, ties by page name; canvas = index in iiif.json's items,
        # None for a page that has no canvas there
        "lowest": [
            {"page": "0002", "quality": 0.4123, "canvas": 1},
            {"page": "0004", "quality": 0.4123, "canvas": 2},
            {"page": "0003", "quality": 0.7, "canvas": None},
            {"page": "0001", "quality": 0.9, "canvas": 0},
        ],
    }


def test_lowest_keeps_five():
    scores = {f"{i:04d}": i / 10 for i in range(1, 10)}
    body = quality.summary(scores, PIPELINE, [])
    assert [e["page"] for e in body["lowest"]] == ["0001", "0002", "0003", "0004", "0005"]
```

In `packages/wrapper/tests/test_store.py`, add (follow that file's existing `cfg`/`s3` fixture usage and
its helper for writing page files; if there is none, write the two files with `tmp_path`):

```python
def test_upload_records_the_page_score_beside_its_size(cfg, s3, tmp_path):
    store = ResultStore(cfg)
    alto = tmp_path / "0001.alto.xml"
    alto.write_text('<alto><Layout><Page WIDTH="10" HEIGHT="20" PC="0.5"/></Layout></alto>')
    page = tmp_path / "0001.page.xml"
    page.write_text("<PcGts/>")
    store.upload_page("0001", {"alto": alto, "page": page})
    assert store.page_quality == {"0001": 0.5}
    assert store.page_dims == {"0001": (10, 20)}


def test_upload_of_an_unscored_page_records_no_score(cfg, s3, tmp_path):
    store = ResultStore(cfg)
    alto = tmp_path / "a.xml"
    alto.write_text('<alto><Layout><Page WIDTH="10" HEIGHT="20" PC="x"/></Layout></alto>')
    page = tmp_path / "p.xml"
    page.write_text("<PcGts/>")
    store.upload_page("0001", {"alto": alto, "page": page})
    assert store.page_quality == {}
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_quality.py packages/wrapper/tests/test_store.py -k "quality or score" -v`
Expected: FAIL with `ModuleNotFoundError: htrflow_batch.quality` and `AttributeError: page_quality`.

- [ ] **Step 3: Implement** `packages/wrapper/src/htrflow_batch/quality.py`

```python
"""Predicted page quality (docs: reference/s3-layout).

htrflow's QualityPrediction step scores each page's transcription and its
ALTO template writes the score as ``Page/@PC`` (ALTO's page confidence,
0-1). It is read here, off the file the wrapper parses on upload anyway, so
the number every reader sees is the one in the ALTO a person opens -- and a
resumed page's score is read back the way its size is. This module is the
one place that knows the score's shape.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence

import yaml

#: What the step predicts: bag-of-words F1 against a ground truth.
TARGET = "bow_f1"
#: How many of a volume's worst pages its summary names.
LOWEST = 5
#: Decimals kept in the published files.
DIGITS = 4


def parse_alto_quality(root: ET.Element) -> float | None:
    """The page's ``PC``, or ``None`` when it has none that is a number in
    [0, 1] -- an absent, empty or hand-edited value is no score, never a
    page failure."""
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "Page":
            continue
        try:
            value = float(elem.get("PC", ""))
        except ValueError:
            return None
        return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None
    return None


def qp_model(pipeline_text: str) -> tuple[str | None, str | None]:
    """The QualityPrediction step's Hub repo and revision, as the pipeline
    names them; ``(None, None)`` when it has no such step."""
    try:
        config = yaml.safe_load(pipeline_text)
    except yaml.YAMLError:
        return None, None
    steps = config.get("steps") if isinstance(config, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict) and str(step.get("step", "")).lower() == "qualityprediction":
            settings = step.get("settings")
            ms = settings.get("model_settings") if isinstance(settings, dict) else None
            ms = ms if isinstance(ms, dict) else {}
            model, revision = ms.get("model"), ms.get("revision")
            return (
                model if isinstance(model, str) else None,
                revision if isinstance(revision, str) else None,
            )
    return None, None


def summary(
    scores: Mapping[str, float], pipeline_text: str, canvases: Sequence[str]
) -> dict | None:
    """The volume's quality block. ``canvases`` is the page names in
    ``iiif.json``'s item order, so a lowest page links straight to its
    canvas; ``None`` with no score at all -- nothing is added then."""
    if not scores:
        return None
    model, revision = qp_model(pipeline_text)
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    index = {name: i for i, name in enumerate(canvases)}
    return {
        "target": TARGET,
        "model": model,
        "revision": revision,
        "mean": round(sum(scores.values()) / len(scores), DIGITS),
        "min": round(ordered[0][1], DIGITS),
        "scored": len(scores),
        "lowest": [
            {"page": name, "quality": round(value, DIGITS), "canvas": index.get(name)}
            for name, value in ordered[:LOWEST]
        ],
    }
```

In `store.py`: import `from .quality import parse_alto_quality`. In `__init__`, after `dimless_pages`:

```python
        #: Each page's predicted quality, off the same parse (quality.py);
        #: a page with no score is simply absent.
        self.page_quality: dict[str, float] = {}
```

At the end of `upload_page`, after the dims `try/except`:

```python
        if (score := parse_alto_quality(roots["alto"])) is not None:
            self.page_quality[name] = score
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_quality.py packages/wrapper/tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/wrapper/src/htrflow_batch/quality.py packages/wrapper/tests/test_quality.py \
  packages/wrapper/src/htrflow_batch/store.py packages/wrapper/tests/test_store.py
git commit -m "feat(wrapper): read each page's predicted quality off its ALTO Page/@PC"
```

---

### Task 4: `manifest.json` and the final `progress.json` carry the scores

**Files:**
- Modify: `packages/wrapper/src/htrflow_batch/publish.py`: `alto_dims` (line 29), `_results_json`
  (line 59), `run_manifest` (line 83), `run` (line 138)
- Modify: `packages/wrapper/src/htrflow_batch/progress.py`: `__init__` and `body`
- Modify: `packages/wrapper/src/htrflow_batch/main.py`, lines 332–338
- Test: `packages/wrapper/tests/test_publish.py`, `packages/wrapper/tests/test_progress.py`,
  `packages/wrapper/tests/test_main.py`
- Docs: `docs/reference/s3-layout.md`, the `manifest.json` and `progress.json` sections

**Interfaces:**
- Consumes: `quality.summary`, `quality.parse_alto_quality`, `ResultStore.page_quality` (Task 3)
- Produces:
  - `publish.Published(NamedTuple)`: `wrote_iiif: bool`, `quality: dict | None`;
    `publish.run(...) -> Published`;
  - `publish.run_manifest(..., quality: Mapping[str, float] | None = None, canvases: Sequence[str] = ())`;
  - `Progress.quality: dict | None`.

- [ ] **Step 1: Write the failing tests**

In `test_publish.py`, add:

```python
QP_PIPELINE = (
    "steps:\n- step: QualityPrediction\n  settings:\n    model_settings:\n"
    "      model: org/qp\n      revision: " + "a" * 40 + "\n"
)


def test_a_run_without_scores_is_the_manifest_it_always_was(cfg, monkeypatch):
    monkeypatch.setattr(publish, "_htrflow_version", lambda: "0.2.3")
    stats = StreamStats(results={"0001": PageOutcome(status="ok", seconds=1.0)})
    args = (cfg, _pages()[:1], stats, "https://m", PIPELINE, 1.0, 1)
    assert publish.run_manifest(*args) == publish.run_manifest(*args, quality={}, canvases=["0001"])
    assert "quality" not in publish.run_manifest(*args)
    assert "quality" not in publish.run_manifest(*args)["results"]["0001"]


def test_scored_pages_carry_their_score_and_the_volume_its_summary(cfg, monkeypatch):
    monkeypatch.setattr(publish, "_htrflow_version", lambda: "0.2.3")
    stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok", seconds=1.0),
            "0002": PageOutcome(status="skipped"),
        }
    )
    body = publish.run_manifest(
        cfg, _pages(), stats, "https://m", QP_PIPELINE, 1.0, 1,
        quality={"0001": 0.81234, "0002": 0.5}, canvases=["0001", "0002"],
    )
    assert body["results"]["0001"]["quality"] == 0.8123
    assert body["results"]["0002"]["quality"] == 0.5
    assert body["quality"]["scored"] == 2
    assert body["quality"]["model"] == "org/qp"
    assert body["quality"]["lowest"][0] == {"page": "0002", "quality": 0.5, "canvas": 1}


def test_an_unscored_page_among_scored_ones_has_no_score_key(cfg, monkeypatch):
    monkeypatch.setattr(publish, "_htrflow_version", lambda: "0.2.3")
    stats = StreamStats(
        results={
            "0001": PageOutcome(status="ok", seconds=1.0),
            "0002": PageOutcome(status="ok", seconds=1.0),
        }
    )
    body = publish.run_manifest(
        cfg, _pages(), stats, "https://m", QP_PIPELINE, 1.0, 1,
        quality={"0001": 0.9}, canvases=["0001", "0002"],
    )
    assert "quality" not in body["results"]["0002"]
    assert body["quality"]["scored"] == 1 and body["pages"] == 2
```

In `test_main.py`, add (it uses the module's `_write_outputs`, `_put_done` and `_keys` helpers):

```python
def _alto_pc(pc: str) -> str:
    return f'<alto><Layout><Page WIDTH="2500" HEIGHT="3538" PC="{pc}"/></Layout></alto>'


def _get_json(s3, cfg, rel):
    return json.loads(
        s3.get_object(Bucket=cfg.s3_bucket, Key=f"demo-v1/SE-RA-1234/{rel}")["Body"].read()
    )


def test_a_scored_run_publishes_its_quality_everywhere(env, cfg, s3):
    scores = {"0001": "0.9", "0002": "0.4", "0003": "0.7"}

    def factory(cfg):
        return lambda path: _write_outputs(cfg, path.stem, alto=_alto_pc(scores[path.stem]))

    assert main(env, process_page_factory=factory) == EXIT_OK
    manifest = _get_json(s3, cfg, "manifest.json")
    assert {n: r["quality"] for n, r in manifest["results"].items()} == {
        "0001": 0.9, "0002": 0.4, "0003": 0.7,
    }
    assert manifest["quality"]["mean"] == 0.6667
    assert manifest["quality"]["lowest"][0]["page"] == "0002"
    progress = _get_json(s3, cfg, "progress.json")
    assert progress["stage"] == "done"
    assert progress["quality"] == manifest["quality"]


def test_a_resumed_page_keeps_its_score(env, cfg, s3):
    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key="demo-v1/SE-RA-1234/alto/0001.xml",
        Body=_alto_pc("0.25").encode(),
    )
    _put_done(s3, cfg, "0001", formats=("page",))

    def factory(cfg):
        return lambda path: _write_outputs(cfg, path.stem, alto=_alto_pc("0.75"))

    assert main(env, process_page_factory=factory) == EXIT_OK
    manifest = _get_json(s3, cfg, "manifest.json")
    assert manifest["results"]["0001"]["status"] == "skipped"
    assert manifest["results"]["0001"]["quality"] == 0.25
    assert manifest["quality"]["scored"] == 3


def test_an_unscored_run_writes_no_quality_anywhere(env, cfg, s3):
    assert main(env, process_page_factory=fake_factory) == EXIT_OK
    assert "quality" not in _get_json(s3, cfg, "manifest.json")
    assert "quality" not in _get_json(s3, cfg, "progress.json")
```

`test_a_resumed_page_keeps_its_score` depends on how resume decides a page is done: it compares the ALTO's
source-digest metadata (`SOURCE_META`, 3096). Check `test_resume_skips_done` in the same file. If `_put_done`
alone makes a page count as done, keep the test as written. If resume needs the source metadata, put the
ALTO with the same `Metadata` that `_put_done`'s caller uses there. Do not weaken the assertions.

In `test_progress.py`, add a test that `Progress.body()` has no `quality` key by default and carries the
dict once `progress.quality = {...}` is set. Follow the constructor usage already in that file.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_publish.py packages/wrapper/tests/test_main.py packages/wrapper/tests/test_progress.py -k "quality or score or scored" -v`
Expected: FAIL (`unexpected keyword argument 'quality'`, `KeyError: 'quality'`).

- [ ] **Step 3: Implement**

`publish.py`:
- Add these imports:

```python
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from . import quality as qp
```

- `alto_dims`: in the read-back loop, parse once and take both facts:

```python
        data = store.get_bytes(f"alto/{p.name}.xml")
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue  # an ALTO that will not parse is left out, not fatal
        # A resumed page's score comes back with its size (quality.py).
        if (score := qp.parse_alto_quality(root)) is not None:
            store.page_quality[p.name] = score
        try:
            dims[p.name] = parse_alto_dims(root)
        except ValueError:
            pass
```

  Import `parse_alto_dims` from `.viewer` (next to `parse_alto_dims_bytes`, which this loop no longer
  uses). Remove `parse_alto_dims_bytes` from the import if nothing else in the file uses it. Update the
  docstring's first sentence to say it also records a read-back page's score.
- `_results_json(stats, quality)`: add `**({"quality": round(quality[n], qp.DIGITS)} if n in quality else {})`
  to each page dict.
- `run_manifest(..., quality: Mapping[str, float] | None = None, canvases: Sequence[str] = ())`:
  - `scores = quality or {}`;
  - pass `scores` to `_results_json`;
  - after building the dict, `if (block := qp.summary(scores, pipeline_text, canvases)) is not None:
    body["quality"] = block`;
  - return `body`, so the key is absent without scores.
- The return type and `run`:

```python
class Published(NamedTuple):
    """What the publish stage tells main: whether iiif.json went out, and
    the volume's quality block (None without scores) for progress.json."""

    wrote_iiif: bool
    quality: dict | None
```

  In `run`:
  - compute `canvases = [p.name for p in pages if p.name in dims]` once, after `alto_dims`;
  - pass `quality=store.page_quality, canvases=canvases` to `run_manifest`;
  - `return Published(wrote_iiif, body.get("quality"))`;
  - update the docstring.

`progress.py`: in `__init__`, add

```python
        #: The volume's quality block, set by main once publish has built it,
        #: so the final progress.json carries what the read API shows --
        #: no second GET of manifest.json. None until then, and absent below.
        self.quality: dict | None = None
```

In `body()`, return `{...existing..., **({"quality": self.quality} if self.quality is not None else {})}`.

`main.py` (lines 332–338):

```python
        published = publish.run(
            cfg, store, source, source_url, pages, stats, uploaded, t_start, nbytes
        )
        if published.wrote_iiif:
            tracker.viewer_published = True
        tracker.quality = published.quality
```

Then grep the tests for `publish.run(` and `wrote_iiif`: `grep -rn "publish.run(\|wrote_iiif" packages/wrapper/tests`.
Update any assertion on the old `bool` return to `.wrote_iiif`.

- [ ] **Step 4: Run the wrapper suite**

Run: `uv run --no-sync pytest packages/wrapper -q`
Expected: all PASS.

- [ ] **Step 5: Document** in `docs/reference/s3-layout.md`:
  - under `manifest.json`, add the per-page `quality` and the top-level `quality` block, with a field
    table (`target`, `model`, `revision`, `mean`, `min`, `scored`, `lowest[] {page, quality, canvas}`);
  - under `progress.json`, add one sentence: the final write carries the same `quality` block.

  Run the docs gate as in Task 1, Step 5.

- [ ] **Step 6: Commit** (three commits)

```bash
git add packages/wrapper/src/htrflow_batch/publish.py packages/wrapper/tests/test_publish.py
git commit -m "feat(wrapper): manifest.json records each scored page and the volume's quality"
git add packages/wrapper/src/htrflow_batch/progress.py packages/wrapper/src/htrflow_batch/main.py \
  packages/wrapper/tests/test_progress.py packages/wrapper/tests/test_main.py
git commit -m "feat(wrapper): the final progress.json carries the volume's quality block"
git add docs/reference/s3-layout.md
git commit -m "docs(reference): quality fields in manifest.json and progress.json"
```

---

### Task 5: `iiif.json` shows the score in the viewer's "More information" panel

**Files:**
- Modify: `packages/wrapper/src/htrflow_batch/viewer.py`, `build_viewer_manifest` (line 62)
- Modify: `packages/wrapper/src/htrflow_batch/publish.py` (`run`), `packages/wrapper/src/htrflow_batch/progress.py` (`_publish_viewer`)
- Test: `packages/wrapper/tests/test_viewer.py`

**Interfaces:**
- Produces: `build_viewer_manifest(cfg, source_manifest, pages, dims, quality: Mapping[str, float] | None = None, summary: dict | None = None) -> dict`

- [ ] **Step 1: Write the failing tests** (in `test_viewer.py`, reusing whatever `cfg` and page fixtures
  its existing `build_viewer_manifest` tests use)

```python
def test_a_scored_canvas_says_its_predicted_quality(cfg):
    pages = [PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})]
    m = build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)}, quality={"0001": 0.87312})
    assert m["items"][0]["metadata"] == [
        {"label": {"en": ["Predicted quality"]}, "value": {"none": ["0.87"]}}
    ]
    assert "metadata" not in m


def test_the_volume_summary_is_manifest_metadata(cfg):
    pages = [PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})]
    summary = {"mean": 0.8123, "min": 0.4, "scored": 12}
    m = build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)}, summary=summary)
    assert m["metadata"] == [
        {
            "label": {"en": ["Predicted quality"]},
            "value": {"none": ["mean 0.81, lowest 0.40, over 12 pages"]},
        }
    ]


def test_without_scores_the_viewer_manifest_is_unchanged(cfg):
    pages = [PageRef(index=1, name="0001", image_url="https://i/1.jpg", canvas={"id": "c1"})]
    plain = build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)})
    assert plain == build_viewer_manifest(cfg, {}, pages, {"0001": (10, 10)}, quality={}, summary=None)
    assert "metadata" not in plain and "metadata" not in plain["items"][0]
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/wrapper/tests/test_viewer.py -k quality -v`
Expected: FAIL (`unexpected keyword argument`).

- [ ] **Step 3: Implement** in `viewer.py`

```python
_QUALITY_LABEL = {"en": ["Predicted quality"]}


def _quality_entry(text: str) -> list[dict]:
    """One IIIF metadata pair; UV lists these in its "More information"
    panel as they are, with no patch."""
    return [{"label": _QUALITY_LABEL, "value": {"none": [text]}}]
```

Add `quality: Mapping[str, float] | None = None, summary: dict | None = None` to `build_viewer_manifest`.
Build each canvas dict as a local `canvas`, then:

```python
        if quality and page.name in quality:
            canvas["metadata"] = _quality_entry(f"{quality[page.name]:.2f}")
        canvases.append(canvas)
```

Build the manifest dict as `manifest = {...}`, then:

```python
    if summary is not None:
        manifest["metadata"] = _quality_entry(
            f"mean {summary['mean']:.2f}, lowest {summary['min']:.2f}, "
            f"over {summary['scored']} pages"
        )
    return manifest
```

Import `Mapping` from `collections.abc`.

The final publish needs the summary before `iiif.json` is written, and `iiif.json` is written before
`manifest.json`. So in `publish.run`, compute `block = qp.summary(store.page_quality, pipeline_text,
canvases)` once, before the `iiif.json` put. Pass `quality=store.page_quality, summary=block` to
`build_viewer_manifest`. That means reading `pipeline_text` before the `iiif.json` put: move the
`read_text` line up, and leave `put_text("pipeline.yaml", …)` where it is. Pass the same `block` into
`run_manifest` through a new optional `summary` parameter, so it is not computed twice. Keep
`run_manifest`'s own computation for callers that pass no summary (the contract script).

In `progress._publish_viewer`, pass `quality=self.store.page_quality` and no summary (the interim
manifest).

- [ ] **Step 4: Run the wrapper suite**

Run: `uv run --no-sync pytest packages/wrapper -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/wrapper/src/htrflow_batch/viewer.py packages/wrapper/src/htrflow_batch/publish.py \
  packages/wrapper/src/htrflow_batch/progress.py packages/wrapper/tests/test_viewer.py
git commit -m "feat(wrapper): iiif.json names each canvas's predicted quality and the volume's"
```

---

### Task 6: The read API passes the quality through and sums it per campaign

**Files:**
- Modify: `packages/web/src/htrflow_web/progress.py`: `_from_progress` (line 103), `_from_manifest`
  (line 128)
- Modify: `packages/web/src/htrflow_web/projection.py`: `_campaign_pages` (line 1087)
- Test: `packages/web/tests/test_progress.py`, `packages/web/tests/test_projection.py`
- Regenerate: `scripts/api_contract.py` → `frontend/src/lib/fixtures/api-contract.json` (`make api-contract`)
- Docs: `docs/reference/web.md`, the section describing a volume's `progress` and the detail response

**Interfaces:**
- Produces, on each volume's `progress` (API camelCase):

```text
"quality": null | {"mean": float, "min": float, "scored": int,
                   "model": str|null, "revision": str|null,
                   "lowest": [{"page": str, "quality": float, "canvas": int|null}]}
```

- Produces, on the detail response (top level, next to `pagesDone`):

```text
"quality": null | {"mean": float, "min": float, "scored": int, "volumes": int,
                   "lowest": [{"volume": str, "page": str, "quality": float,
                               "canvas": int|null, "iiifUrl": str}]}
```

- [ ] **Step 1: Write the failing tests**

In `test_progress.py` (use its existing way of calling `_from_progress`):

```python
from htrflow_web.progress import _from_manifest, _from_progress, _quality

GOOD = {
    "target": "bow_f1", "model": "org/qp", "revision": "a" * 40,
    "mean": 0.8, "min": 0.4, "scored": 3,
    "lowest": [{"page": "0002", "quality": 0.4, "canvas": 1}],
}


def test_a_quality_block_passes_through():
    assert _quality(GOOD) == {
        "mean": 0.8, "min": 0.4, "scored": 3, "model": "org/qp", "revision": "a" * 40,
        "lowest": [{"page": "0002", "quality": 0.4, "canvas": 1}],
    }


@pytest.mark.parametrize(
    "bad",
    [
        None, "0.8", [], {},
        {**GOOD, "mean": 7},
        {**GOOD, "mean": True},
        {**GOOD, "scored": -1},
        {**GOOD, "min": float("nan")},
    ],
)
def test_a_malformed_quality_block_is_none(bad):
    assert _quality(bad) is None


def test_lowest_is_clipped_and_bad_entries_dropped():
    many = [{"page": f"{i:04d}", "quality": 0.1, "canvas": i} for i in range(10_000)]
    block = _quality({**GOOD, "lowest": [{"page": 3}, *many]})
    assert len(block["lowest"]) == 5
    assert block["lowest"][0]["page"] == "0000"


def test_progress_and_manifest_both_carry_it():
    doc = {"pages_total": 3, "pages_done": 3, "quality": GOOD}
    assert _from_progress(doc, 0.0)["quality"]["mean"] == 0.8
    assert _from_progress({"pages_total": 3}, 0.0)["quality"] is None
    manifest = {"pages": 1, "results": {"0001": {"status": "ok"}}, "quality": GOOD}
    assert _from_manifest(manifest, 0.0)["quality"]["scored"] == 3
```

In `test_projection.py`:

```python
from htrflow_web.projection import _campaign_quality


def _row(vid, q):
    return {
        "id": vid,
        "iiifUrl": f"https://pub/{vid}/iiif.json",
        "progress": {"quality": q},
    }


def test_the_campaign_mean_is_weighted_by_scored_pages():
    rows = [
        _row("a", {"mean": 0.9, "min": 0.8, "scored": 1, "lowest": [{"page": "1", "quality": 0.8, "canvas": 0}]}),
        _row("b", {"mean": 0.5, "min": 0.2, "scored": 3, "lowest": [{"page": "7", "quality": 0.2, "canvas": 6}]}),
        _row("c", None),
    ]
    q = _campaign_quality(rows)
    assert q["mean"] == 0.6 and q["min"] == 0.2 and q["scored"] == 4 and q["volumes"] == 2
    assert q["lowest"][0] == {
        "volume": "b", "page": "7", "quality": 0.2, "canvas": 6,
        "iiifUrl": "https://pub/b/iiif.json",
    }


def test_no_scored_volume_is_no_campaign_quality():
    assert _campaign_quality([_row("a", None)]) is None
    assert _campaign_quality([]) is None
```

`_campaign_quality` is called with rows whose `progress` is not `None`, as `_campaign_pages` is. Rows
whose progress lacks the key are treated as unscored.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --no-sync pytest packages/web/tests/test_progress.py packages/web/tests/test_projection.py -k quality -v`
Expected: FAIL (`ImportError: cannot import name '_quality'`).

- [ ] **Step 3: Implement**

`progress.py`:

```python
#: A volume's quality block names at most this many of its worst pages; the
#: wrapper writes five (quality.LOWEST), anything longer is not ours.
MAX_LOWEST = 5


def _score(value: object) -> float | None:
    ok = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )
    return float(value) if ok else None


def _quality(value: object) -> dict | None:
    """The wrapper's quality block (docs: reference/s3-layout), or nothing.
    A mean outside [0, 1], a negative count or a string is not one of ours:
    dropped whole rather than drawn."""
    if not isinstance(value, dict):
        return None
    mean, low = _score(value.get("mean")), _score(value.get("min"))
    scored = value.get("scored")
    if mean is None or low is None or not isinstance(scored, int) or isinstance(scored, bool) or scored < 1:
        return None
    lowest = []
    for entry in value.get("lowest") if isinstance(value.get("lowest"), list) else []:
        if len(lowest) == MAX_LOWEST:
            break
        if not isinstance(entry, dict):
            continue
        q, page, canvas = _score(entry.get("quality")), _str_or_none(entry.get("page")), entry.get("canvas")
        if q is None or page is None:
            continue
        ok_canvas = isinstance(canvas, int) and not isinstance(canvas, bool) and canvas >= 0
        lowest.append({"page": page, "quality": q, "canvas": canvas if ok_canvas else None})
    return {
        "mean": mean,
        "min": low,
        "scored": scored,
        "model": _str_or_none(value.get("model")),
        "revision": _str_or_none(value.get("revision")),
        "lowest": lowest,
    }
```

Add `import math`. Add `"quality": _quality(doc.get("quality")),` to both `_from_progress` and
`_from_manifest` dicts.

`projection.py`, next to `_campaign_pages`:

```python
#: The campaign's lowest pages, across every volume read.
CAMPAIGN_LOWEST = 5


def _campaign_quality(rows: list[dict]) -> dict | None:
    """The campaign's predicted quality over the volumes whose progress
    carries one: the mean weighted by each volume's scored pages, and the
    worst pages anywhere, each with the viewer manifest it opens in.
    ``volumes`` says how many volumes it covers; the card says so when that
    is not all of them."""
    scored = [
        (row, q)
        for row in rows
        if (q := (row.get("progress") or {}).get("quality")) is not None
    ]
    if not scored:
        return None
    pages = sum(q["scored"] for _, q in scored)
    lowest = sorted(
        (
            {"volume": row["id"], **entry, "iiifUrl": row["iiifUrl"]}
            for row, q in scored
            for entry in q["lowest"]
        ),
        key=lambda e: (e["quality"], e["volume"], e["page"]),
    )[:CAMPAIGN_LOWEST]
    return {
        "mean": round(sum(q["mean"] * q["scored"] for _, q in scored) / pages, 4),
        "min": min(q["min"] for _, q in scored),
        "scored": pages,
        "volumes": len(scored),
        "lowest": lowest,
    }
```

In `_campaign_pages`, add `"quality": _campaign_quality(rows),` to its return dict. It is then on every
response `_campaign_pages` feeds.

- [ ] **Step 4: Regenerate the contract and run the web suite**

Extend `scripts/api_contract.py` so that one volume of its fake campaign has a `progress.json` carrying
the `GOOD`-shaped `quality` block. Find where it builds its progress documents
(`grep -n "pages_total" scripts/api_contract.py`) and add the key to one of them.

Run: `make api-contract && uv run --no-sync pytest packages/web -q`
Expected: the fixture changes (`git diff --stat frontend/src/lib/fixtures/api-contract.json` shows it),
and all tests PASS, `test_contract.py` included.

- [ ] **Step 5: Document** the two `quality` shapes in `docs/reference/web.md`, where the volume `progress`
  and detail fields are described. Run the docs gate.

- [ ] **Step 6: Commit** (two commits)

```bash
git add packages/web/src/htrflow_web/progress.py packages/web/src/htrflow_web/projection.py \
  packages/web/tests/test_progress.py packages/web/tests/test_projection.py
git commit -m "feat(web): the read API passes each volume's quality through and sums it per campaign"
git add scripts/api_contract.py frontend/src/lib/fixtures/api-contract.json docs/reference/web.md
git commit -m "test(contract): a scored campaign in the API contract; document the quality fields"
```

The frontend's api-contract vitest still passes after this commit: the Zod objects are not strict, so
the new key is dropped until Task 8 declares it. Run `make frontend-test` to confirm before committing.

---

### Task 7: The run viewer's pages table sorts by quality

**Files:**
- Create: `frontend/src/lib/quality.ts`, `frontend/src/lib/quality.test.ts`
- Modify: `frontend/src/lib/run.ts` (`pageResultSchema`, `runManifestSchema`)
- Modify: `frontend/src/lib/components/PagesTable.svelte`
- Test: `frontend/src/lib/components/PagesTable.test.ts`
- Regenerate: `scripts/wrapper_contract.py` → `frontend/src/lib/fixtures/wrapper-contract.json`
  (`make wrapper-contract`)

**Interfaces:**
- Produces:
  - `formatQuality(q: number | null | undefined): string` (`"0.87"`, or `""`);
  - `hasQualityStep(steps: readonly string[]): boolean`;
  - `sortByQuality<T extends { id: string; quality?: number }>(rows: readonly T[]): T[]`
    (ascending, unscored last, ties by id).
- Produces: `PageResult.quality?: number`

- [ ] **Step 1: Write the failing tests**

`frontend/src/lib/quality.test.ts`:

```ts
import { describe, expect, test } from "vitest";
import { formatQuality, hasQualityStep, sortByQuality } from "./quality.js";

describe("quality", () => {
  test("two decimals, empty when there is none", () => {
    expect(formatQuality(0.87312)).toBe("0.87");
    expect(formatQuality(1)).toBe("1.00");
    expect(formatQuality(null)).toBe("");
    expect(formatQuality(undefined)).toBe("");
  });

  test("the step is found by htrflow's own case-blind name", () => {
    expect(hasQualityStep(["Segmentation", "QualityPrediction"])).toBe(true);
    expect(hasQualityStep(["qualityprediction"])).toBe(true);
    expect(hasQualityStep(["Segmentation"])).toBe(false);
  });

  test("worst first, unscored last, ties by id", () => {
    const rows = [
      { id: "c", quality: 0.5 },
      { id: "a" },
      { id: "b", quality: 0.2 },
      { id: "d", quality: 0.5 },
    ];
    expect(sortByQuality(rows).map((r) => r.id)).toEqual(["b", "c", "d", "a"]);
  });
});
```

Append to `PagesTable.test.ts`:

```ts
describe("PagesTable quality column", () => {
  test("no scored page: no quality column", () => {
    render(PagesTable, { pages: [page()] });
    expect(screen.queryByRole("columnheader", { name: /quality/ })).toBeNull();
  });

  test("scored pages show two decimals and sort worst first on request", async () => {
    render(PagesTable, {
      pages: [
        page({ id: "0001", quality: 0.9 }),
        page({ id: "0002", quality: 0.31 }),
        page({ id: "0003" }),
      ],
    });
    const cells = () =>
      screen.getAllByRole("row").slice(1).map((r) => r.querySelector("td")?.textContent?.trim());
    expect(cells()).toEqual(["0001", "0002", "0003"]);
    await fireEvent.click(screen.getByRole("button", { name: /quality/ }));
    expect(cells()).toEqual(["0002", "0001", "0003"]);
    expect(screen.getByText("0.31")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: /quality/ }));
    expect(cells()).toEqual(["0001", "0002", "0003"]);
  });
});
```

- [ ] **Step 2: Run them and see them fail**

Run: `cd frontend && bun run test -- quality PagesTable`
Expected: FAIL (module not found; no quality button).

- [ ] **Step 3: Implement**

`frontend/src/lib/quality.ts`:

```ts
// Predicted page quality: htrflow's QualityPrediction step scores each
// page 0-1 (a predicted bag-of-words F1). No colour and no threshold here:
// what counts as poor depends on the material, and is not this page's call.

export function formatQuality(q: number | null | undefined): string {
  return typeof q === "number" && Number.isFinite(q) ? q.toFixed(2) : "";
}

/** htrflow resolves a step by its lower-cased name, so this does too. */
export function hasQualityStep(steps: readonly string[]): boolean {
  return steps.some((s) => s.toLowerCase() === "qualityprediction");
}

/** Worst first; a page with no score after every scored one; ties by id. */
export function sortByQuality<T extends { id: string; quality?: number }>(
  rows: readonly T[],
): T[] {
  return [...rows].sort((a, b) => {
    const qa = a.quality ?? Number.POSITIVE_INFINITY;
    const qb = b.quality ?? Number.POSITIVE_INFINITY;
    return qa !== qb ? qa - qb : a.id.localeCompare(b.id);
  });
}
```

`run.ts`:
- `pageResultSchema` gains `quality: z.number().optional(),`;
- `runManifestSchema` gains `quality: z.object({ mean: z.number(), min: z.number(), scored: z.number() }).loose().optional(),`
  with a comment naming `publish.py`.

`PagesTable.svelte`:
- import `formatQuality` and `sortByQuality`;
- add state and derivations:

```ts
  let byQuality = $state(false);
  const scored = $derived(pages.some((p) => p.quality !== undefined));
  const ordered = $derived(byQuality ? sortByQuality(pages) : pages);
  const slice = $derived(ordered.slice(offset, last));
```

  (replace the existing `slice`);
- update the caption to include `quality` when `scored`;
- after the `seconds` header, add:

```svelte
        {#if scored}
          <th scope="col" class="num" aria-sort={byQuality ? "ascending" : "none"}>
            <button type="button" class="sort" onclick={() => { byQuality = !byQuality; offset = 0; }}
              >quality{byQuality ? " ▲" : ""}</button>
          </th>
        {/if}
```

- in each body row, after the seconds cell:
  `{#if scored}<td class="num">{formatQuality(r.quality)}</td>{/if}`;
- add CSS for `.sort` matching the header text: `font: inherit; color: inherit; background: none;
  border: 0; padding: 0; cursor: pointer; text-transform: inherit; letter-spacing: inherit;`.

`scripts/wrapper_contract.py`: add a second manifest, `"manifestScored"`, built like `_manifest` but
passing `quality={"0001": 0.9123, "0003": 0.41, "0004": 0.7}, canvases=["0001", "0003", "0004"]`. Use a
`PIPELINE` variant that has a QualityPrediction step with `model: org/qp-model` and a 40-hex revision.
In `frontend/src/lib/fixtures/wrapper-contract.test.ts`, add a test that `runManifestSchema.parse`s it
and that `pageStats` carries `quality` through.

- [ ] **Step 4: Regenerate and run**

Run: `make wrapper-contract && uv run --no-sync pytest packages/wrapper/tests/test_contract.py -q && cd frontend && bun run test && bun run check`
Expected: all PASS; `svelte-check` 0 errors.

- [ ] **Step 5: Commit** (two commits)

```bash
git add frontend/src/lib/quality.ts frontend/src/lib/quality.test.ts frontend/src/lib/run.ts \
  frontend/src/lib/components/PagesTable.svelte frontend/src/lib/components/PagesTable.test.ts
git commit -m "feat(frontend): the run viewer's pages table shows and sorts by predicted quality"
git add scripts/wrapper_contract.py frontend/src/lib/fixtures/wrapper-contract.json \
  frontend/src/lib/fixtures/wrapper-contract.test.ts
git commit -m "test(contract): a scored manifest in the wrapper contract"
```

---

### Task 8: The campaign card has a quality column, a campaign mean and the lowest pages

**Files:**
- Modify: `frontend/src/lib/api.ts` (`volumeProgressSchema` line 197, `jobDetailSchema` line 262)
- Modify: `frontend/src/lib/components/CampaignCard.svelte`
- Test: `frontend/src/lib/components/CampaignCard.test.ts`, `frontend/src/lib/fixtures/api-contract.test.ts`

**Interfaces:**
- Consumes: `formatQuality` and `hasQualityStep` (Task 7); the API shapes (Task 6)
- Produces: `VolumeProgress.quality`, `JobDetail.quality`

- [ ] **Step 1: Write the failing tests** (in `CampaignCard.test.ts`, inside `describe("CampaignCard")`,
  using its `job`, `volumeDone`, `detailBase`, `jsonResponse` and `expand` helpers). Mock `fetch` the way
  the neighbouring tests do; see "fetches its own volumes from the read API" at line 164.

```ts
  const progressDone = {
    done: 3, total: 3, failed: 0, lastPage: "0003", stage: "done",
    updatedAt: null, ageSeconds: null, lastError: null, errors: 0,
    viewerPublished: true,
  };
  const scoredVolume = {
    ...volumeDone,
    progress: {
      ...progressDone,
      quality: {
        mean: 0.8123, min: 0.41, scored: 3, model: "org/qp", revision: null,
        lowest: [{ page: "0002", quality: 0.41, canvas: 1 }],
      },
    },
  };
  const qpSteps = ["Segmentation", "Segmentation", "TextRecognition", "QualityPrediction"];

  test("a QP campaign shows each volume's quality, the campaign mean and the lowest pages", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      ...job, ...detailBase, pipelineSteps: qpSteps,
      volumes: [scoredVolume], failures: [],
      pagesCoverage: { counted: 1, of: 1 },
      quality: {
        mean: 0.8123, min: 0.41, scored: 3, volumes: 1,
        lowest: [{ volume: "vol0", page: "0002", quality: 0.41, canvas: 1, iiifUrl: volumeDone.iiifUrl }],
      },
    })));
    render(CampaignCard, { job: { ...job, counts: { ...job.counts, total: 2 } } });
    await expand();
    expect(await screen.findAllByText("0.81")).not.toHaveLength(0);
    const low = screen.getByRole("link", { name: /vol0 0002/ });
    expect(low).toHaveAttribute(
      "href",
      "uv.html#?manifest=" + encodeURIComponent(volumeDone.iiifUrl) + "&cv=1",
    );
  });

  test("a partial mean says how many volumes it covers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      ...job, ...detailBase, pipelineSteps: qpSteps,
      volumes: [scoredVolume], failures: [],
      pagesCoverage: { counted: 1, of: 3 },
      quality: { mean: 0.8, min: 0.41, scored: 3, volumes: 1, lowest: [] },
    })));
    render(CampaignCard, { job });
    await expand();
    expect(await screen.findByText(/scored in 1 of 3 volumes/)).toBeInTheDocument();
  });

  test("a QP campaign with nothing scored yet keeps the column and draws no lowest line", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      ...job, ...detailBase, pipelineSteps: qpSteps,
      volumes: [{ ...volumeDone, progress: { ...progressDone, quality: null } }],
      failures: [], quality: null,
    })));
    const { container } = render(CampaignCard, { job });
    await expand();
    await screen.findByText("vol0");
    expect(container.querySelector(".card-body.with-quality")).not.toBeNull();
    expect(screen.queryByText(/lowest predicted quality/)).toBeNull();
  });

  test("a campaign without a QP step has no quality track", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      ...job, ...detailBase, volumes: [volumeDone], failures: [], quality: null,
    })));
    const { container } = render(CampaignCard, { job });
    await expand();
    await screen.findByText("vol0");
    expect(container.querySelector(".card-body.with-quality")).toBeNull();
    expect(container.querySelector(".c-quality")).toBeNull();
  });

  test("the quality track is fixed-width at every size", () => {
    const withQ = tracks(cssOf(".card-body.with-quality .row")["grid-template-columns"]);
    expect(withQ).toContain("var(--quality)");
    const phone = tracks(cssOf(".card-body.with-quality .row", PHONE)["grid-template-columns"]);
    expect(phone).toContain("var(--quality)");
  });
```

Use the file's existing `cssOf`/`tracks` helpers. If `cssOf` returns declarations in another shape,
follow how the existing grid tests use it (`grep -n "grid-template-columns" CampaignCard.test.ts`).

- [ ] **Step 2: Run them and see them fail**

Run: `cd frontend && bun run test -- CampaignCard`
Expected: the five new tests FAIL.

- [ ] **Step 3: Implement**

`api.ts`:

```ts
// The wrapper's predicted page quality (docs: reference/web): null without
// a QualityPrediction step, or before the volume has published.
export const volumeQualitySchema = z.object({
  mean: z.number(),
  min: z.number(),
  scored: z.number(),
  model: z.string().nullable(),
  revision: z.string().nullable(),
  lowest: z.array(
    z.object({ page: z.string(), quality: z.number(), canvas: z.number().nullable() }),
  ),
});
export const campaignQualitySchema = z.object({
  mean: z.number(),
  min: z.number(),
  scored: z.number(),
  volumes: z.number(),
  lowest: z.array(
    z.object({
      volume: z.string(),
      page: z.string(),
      quality: z.number(),
      canvas: z.number().nullable(),
      iiifUrl: httpUrlSchema,
    }),
  ),
});
```

- `volumeProgressSchema` gains `quality: volumeQualitySchema.nullable().catch(null),`. A block this page
  cannot read is no number; it does not refuse the whole campaign (the `sourceUrl` precedent).
- `jobDetailSchema` gains `quality: campaignQualitySchema.nullable().catch(null),`.
- Export `type CampaignQuality = z.infer<typeof campaignQualitySchema>`.

`CampaignCard.svelte`:
- Script:
  - import `formatQuality` and `hasQualityStep` from `$lib/quality.js`, and `CampaignQuality`;
  - add `let quality = $state<CampaignQuality | null>(null);` and set it wherever the detail response
    is applied (next to `notice = …`; find it with `grep -n "notice = " CampaignCard.svelte`);
  - add `const withQuality = $derived(quality !== null || hasQualityStep(pipelineSteps));`;
  - add a viewer link for a lowest page:

```ts
  function lowHref(l: CampaignQuality["lowest"][number]): string {
    return (
      `uv.html#?manifest=${encodeURIComponent(l.iiifUrl)}` +
      (l.canvas === null ? "" : `&cv=${l.canvas}`)
    );
  }
```

- Markup:
  - add `class:with-quality={withQuality}` to `<div class="card-body">`;
  - in `volumeRow`, after the `c-fraction` span:
    `{#if withQuality}<span class="c-quality" role={cellRole}>{formatQuality(v.progress?.quality?.mean)}</span>{/if}`;
  - in `totalsRow`, after its `c-fraction`:
    `{#if withQuality}<span class="c-quality">{label === "pages" ? formatQuality(quality?.mean) : ""}</span>{/if}`;
  - in the placeholder rows: `{#if withQuality}<span class="c-quality">&nbsp;</span>{/if}`;
  - in the sr-only head row: `{#if withQuality}<span role="columnheader">quality</span>{/if}`.
- After the totals rows, still inside `{#if showTotals}`, but also render it for a single-volume campaign.
  Place it just before the `problems` paragraph and outside `showTotals`:

```svelte
        {#if quality !== null && (quality.lowest.length > 0 || quality.volumes < coverage.of)}
          <p class="quality-line">
            {#if quality.lowest.length > 0}
              lowest predicted quality:
              {#each quality.lowest as l, i (l.volume + "/" + l.page)}
                {i > 0 ? " · " : ""}<a href={lowHref(l)} aria-label="{l.volume} {l.page}, {formatQuality(l.quality)}"
                  >{l.volume}/{l.page}</a> {formatQuality(l.quality)}
              {/each}
            {/if}
            {#if quality.volumes < coverage.of}
              <span class="quiet">{quality.lowest.length > 0 ? " · " : ""}scored in {quality.volumes} of {coverage.of} volumes</span>
            {/if}
          </p>
        {/if}
```

- CSS:
  - add `--quality: 3rem;` beside `--fraction` in `.campaign`;
  - add the grid:

```css
  .card-body.with-quality .row {
    grid-template-columns:
      minmax(6rem, 1fr) var(--icon) var(--bar) var(--fraction)
      var(--quality) var(--pill) var(--icon);
  }

  .c-quality {
    font-variant-numeric: tabular-nums;
    color: var(--foreground);
    white-space: nowrap;
  }

  .quality-line {
    margin: 0.2rem 0;
    font-size: 0.74rem;
    color: var(--muted-foreground);
    overflow-wrap: anywhere;
  }
```

  - in the `@media (max-width: 520px)` block:

```css
    .card-body.with-quality .row {
      grid-template-columns:
        0 var(--icon) minmax(2.5rem, var(--bar)) var(--fraction)
        var(--quality) var(--pill) var(--icon);
      grid-template-areas:
        "label label label     label    label   label  label"
        ".     links bar       fraction quality status log"
        ".     .     lost      lost     lost    lost   ."
        "note  note  note      note     note    note   note";
    }

    .c-quality {
      grid-area: quality;
    }
```

  `.row-note` uses `grid-column: 1 / -1`, so it spans seven tracks unchanged.

`api-contract.test.ts`: add an assertion that the scored volume's `progress.quality.mean` from Task 6
parses as a number, and that the detail's `quality.volumes` is `1`.

- [ ] **Step 4: Run the frontend suite, the type check and the layout-shift script**

Run: `cd frontend && bun run test && bun run check`
Expected: all PASS, 0 errors.

Run: `node frontend/scripts/measure-shifts.mjs --help`, then run it the way its header comment says,
against `bun run build` output. Expected: no new shift on an open card, compared with the same script
on `main`. Record both numbers in the commit message body.

- [ ] **Step 5: Commit** (two commits)

```bash
git add frontend/src/lib/api.ts frontend/src/lib/fixtures/api-contract.test.ts
git commit -m "feat(frontend): the API schemas read each volume's and the campaign's quality"
git add frontend/src/lib/components/CampaignCard.svelte frontend/src/lib/components/CampaignCard.test.ts
git commit -m "feat(frontend): a quality column, the campaign mean and its lowest pages on the card"
```

---

### Task 9: UV4's text panel shows the page's predicted quality

**Files:**
- Modify: `.docker/uv4-uv-html.patch` (the preamble and the `TextRightPanel.ts` hunks)
- Create: `packages/web/tests/test_uv4_patch.py`

**Interfaces:**
- Consumes: ALTO `Page/@PC`, written by htrflow at the image switch. Until then, a fixture ALTO.

- [ ] **Step 1: Write the failing test** (`packages/web/tests/test_uv4_patch.py`)

```python
"""The UV4 patch's text panel reads the page's predicted quality off the
ALTO it already fetched, and inserts it as text (the patch's XSS rule)."""

from pathlib import Path

PATCH = Path(__file__).resolve().parents[3] / ".docker" / "uv4-uv-html.patch"


def _added(text: str) -> str:
    return "\n".join(
        line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def test_the_text_panel_reads_page_pc_and_sets_it_as_text():
    added = _added(PATCH.read_text())
    assert 'getAttribute("PC")' in added
    block = added[added.index('getAttribute("PC")') :][:600]
    assert ".text(" in block
    assert ".html(" not in block
    assert "Predicted quality" in block


def test_the_preamble_says_what_the_quality_hunk_does():
    preamble = PATCH.read_text().split("diff --git", 1)[0]
    assert "predicted quality" in preamble.lower()
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --no-sync pytest packages/web/tests/test_uv4_patch.py -v`
Expected: FAIL (`'getAttribute("PC")' in added` is false).

- [ ] **Step 3: Regenerate the patch with the change**

```bash
cd "$SCRATCH" && git clone https://github.com/Riksarkivet/universalviewer4 uv4 && cd uv4
git checkout f2e8f66d3bd5a69e8e392764204d13d9524f63b2
git apply /path/to/worktree/.docker/uv4-uv-html.patch
```

(`$SCRATCH` is any scratch directory outside the repo.)

In `src/content-handlers/iiif/modules/uv-textrightpanel-module/TextRightPanel.ts`, find the block that
the patch turned into `$('<div class="label"></div>').text(header)`. Directly after that `if (header)
{ … }` block, add:

```ts
      // htrflow's QualityPrediction step writes the page's score as
      // Page/@PC (0-1). Read off this same ALTO, so the number always
      // belongs to the text under it; set with .text(), like every other
      // ALTO value in this panel. No PC, nothing shown.
      const pc = altoPage ? altoPage.getAttribute("PC") : null;
      const score = pc === null || pc === "" ? NaN : Number(pc);
      if (Number.isFinite(score) && score >= 0 && score <= 1) {
        this.$transcribedText.append(
          $('<div class="label quality"></div>').text(
            "Predicted quality " + score.toFixed(2)
          )
        );
      }
```

(`altoPage` is the `const` the patch already declares for the WIDTH/HEIGHT scaling, in the same
function.)

Regenerate the patch: `git diff > /tmp/uv4.diff`. Then rebuild `.docker/uv4-uv-html.patch` as the
existing preamble, plus a new bullet, plus the diff:

```text
- TextRightPanel.ts (also): show the page's predicted quality, ALTO
  Page/@PC as htrflow's QualityPrediction writes it, under the header --
  read off the same ALTO as the lines, set as text.
```

Keep the preamble's existing bullets word for word.

- [ ] **Step 4: Run the test, then prove the patch applies in the real build**

Run: `uv run --no-sync pytest packages/web/tests/test_uv4_patch.py -v`
Expected: PASS.

Run: `make build-web`
Expected: the `uv4` stage's `git apply /tmp/uv4.patch` succeeds and the image builds.

- [ ] **Step 5: See it in a browser.** Serve the built web image locally. Open `uv.html` on an `iiif.json`
  whose `seeAlso` ALTO has `<Page … PC="0.87">`. The compose stack from `make compose-up` gives you a
  bucket and a published volume. Re-upload that volume's ALTO with a `PC` attribute added.
  Expected:
  - the text panel shows "Predicted quality 0.87" under the header;
  - "More information" shows the canvas metadata from Task 5;
  - an ALTO with `PC="<img src=x onerror=alert(1)>"` shows nothing and runs nothing.

  Take a screenshot for the PR.

- [ ] **Step 6: Commit**

```bash
git add .docker/uv4-uv-html.patch packages/web/tests/test_uv4_patch.py
git commit -m "feat(web): UV4's text panel shows the page's predicted quality from its ALTO"
```

---

### Task 10: Whole-branch gate and PR

- [ ] **Step 1:** `make ci && make frontend-test frontend-check && scripts/docs-site.sh build --clean --strict`
  Expected: all green. `scripts/loc-budget.sh` is part of `make ci`. If a file goes over its budget, split
  the new code out rather than raising the budget. `quality.py` exists for this reason.
- [ ] **Step 2:** Push the branch and open a PR titled "Quality prediction: scores from ALTO through
  manifest, API, browser and viewer". The body must:
  - list the three refinements from this plan's header;
  - list the held items below;
  - include the Task 9 screenshot.

---

## Held until upstream merges the step (not executed now)

These depend on the merged step's interface. Each gets its own short plan when upstream lands.

1. **The image switch** (spec §1):
   - pin htrflow to the upstream commit;
   - add `quality-prediction` and xgboost (measure `xgboost-cpu` against the full wheel for identical
     predictions and the image size);
   - prove it with three runs: test-driver-real with a real QP model, the empty-output guard (a flat
     pipeline still fails loudly), and a GPU htr_demo run on local k3s with the browser and viewer
     showing numbers.
2. **The wrapper's Hub-resolution layer** (spec §3): only if the merged step still takes local paths.
   - Warm-up: `hf_hub_download` of `model_file` and `bin_config_file` at `revision`.
   - Pod: `HF_HUB_OFFLINE=1` resolves them from the cache, and the step's settings are rewritten to the
     constructor's `model=`/`bin_config=`.
   - A missing file or a model the package cannot load → `EXIT_PERMANENT`.
   - If upstream accepts the Hub reference, drop this item. The warm-up's pipeline construction already
     downloads it, as for every other model.
3. **Provenance** (spec §3): the ALTO's htrflow-batch `<Processing>` block names the QP repo, revision and
   files. This is only needed if htrflow's own `<Processing>` block does not already name them for this step.
   Check against the merged step's `StepMetadata`.
4. **PAGE XML**: whatever upstream settles on for the score (request 2 to the step's author). If it lands,
   `exportcheck`'s view of PAGE is unaffected (it reads text only). There is no wrapper change.
