"""The QualityPrediction step's model, from a pinned Hub reference to the
files htrflow's step takes (driver._resolve_quality_models).

The step takes ``model=`` and ``bin_config=`` as paths; a campaign pipeline
names a Hub repo, commit and two file names, and the wrapper downloads them
(warm-up) or finds them in the cache (batch pod) before htrflow builds it.
"""

from __future__ import annotations

import httpx
import pytest
import yaml
from huggingface_hub.errors import LocalEntryNotFoundError, RemoteEntryNotFoundError

from htrflow_batch import driver

REPO = "Riksarkivet/xgboost_quality_prediction_swedish_lion_region_line"
SHA = "f4241df05a189831ec4b3f1d92df9728d3752495"
QP = {
    "model": REPO,
    "revision": SHA,
    "model_file": "xgb_hpo_json_model_only_target_bow_f1.joblib",
    "bin_config_file": "sl26eval_region_line_bin_config.json",
}


def _pipeline(tmp_path, qp_settings: dict) -> str:
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "steps": [
                    {"step": "TextRecognition", "settings": {"model": "TrOCR"}},
                    {"step": "QualityPrediction", "settings": qp_settings},
                ]
            }
        )
    )
    return str(path)


@pytest.fixture
def built(fake_htrflow) -> list:
    """A fake htrflow recording the pipeline FILE it was asked to build, read
    at that moment (the wrapper's resolved copy lives only for the build)."""
    seen: list = []

    class Pipeline:
        def __init__(self, steps):
            self.steps = steps

        @staticmethod
        def from_config(path):
            with open(path) as f:
                seen.append(yaml.safe_load(f))
            return Pipeline([])

    fake_htrflow(pipeline={"Pipeline": Pipeline}, steps={})
    return seen


@pytest.fixture
def hub(monkeypatch) -> list:
    """``hf_hub_download`` answering from a pretend cache; records each call."""
    calls: list = []

    def download(repo_id, filename, *, revision=None, **_):
        calls.append((repo_id, filename, revision))
        return f"/data/hf/hub/{repo_id}/snapshots/{revision}/{filename}"

    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    return calls


def test_the_hub_reference_becomes_the_steps_file_paths(tmp_path, built, hub):
    path = _pipeline(tmp_path, {"model_settings": QP, "feature_groups": ["text"]})

    driver.build_pipeline(path)

    assert hub == [
        (REPO, QP["model_file"], SHA),
        (REPO, QP["bin_config_file"], SHA),
    ]
    [config] = built
    qp = config["steps"][1]
    assert qp == {
        "step": "QualityPrediction",
        "settings": {
            "feature_groups": ["text"],
            "model": f"/data/hf/hub/{REPO}/snapshots/{SHA}/{QP['model_file']}",
            "bin_config": f"/data/hf/hub/{REPO}/snapshots/{SHA}/{QP['bin_config_file']}",
        },
    }
    # The other steps go through untouched, and the file as written is not.
    assert config["steps"][0] == {"step": "TextRecognition", "settings": {"model": "TrOCR"}}
    assert yaml.safe_load(open(path))["steps"][1]["settings"]["model_settings"] == QP


def test_a_pipeline_without_the_step_is_built_from_its_own_file(
    tmp_path, fake_htrflow, hub
):
    paths: list = []

    class Pipeline:
        @staticmethod
        def from_config(path):
            paths.append(path)
            return Pipeline()

    fake_htrflow(pipeline={"Pipeline": Pipeline}, steps={})
    path = tmp_path / "pipeline.yaml"
    path.write_text("steps:\n  - step: TextRecognition\n    settings: {model: TrOCR}\n")

    driver.build_pipeline(str(path))

    assert paths == [str(path)] and hub == []


@pytest.mark.parametrize(
    "qp_settings, reason",
    [
        ({"model": "/home/someone/model.joblib", "bin_config": "/x.json"}, "never a path"),
        ({"model_settings": QP | {"revision": "main"}}, "40-hex commit"),
        ({"model_settings": QP | {"model_file": "../model.joblib"}}, "plain file name"),
        ({"model_settings": QP | {"extra": "x"}}, "exactly"),
        ({"model_settings": QP, "model": "/elsewhere.joblib"}, "set by the wrapper"),
    ],
    ids=["local-paths", "branch-not-commit", "path-in-file", "unknown-key", "path-beside"],
)
def test_a_step_that_is_not_a_pinned_hub_reference_is_refused(
    tmp_path, built, hub, qp_settings, reason
):
    """Permanent (ValueError), before anything is downloaded or built: the
    model is a pickle, so a path or a moving ref is never loaded."""
    with pytest.raises(ValueError, match=reason):
        driver.build_pipeline(_pipeline(tmp_path, qp_settings))
    assert hub == [] and built == []


def test_a_file_the_commit_does_not_have_is_a_pipeline_mistake(
    tmp_path, built, monkeypatch
):
    def download(repo_id, filename, *, revision=None, **_):
        raise RemoteEntryNotFoundError("404", response=httpx.Response(404, request=httpx.Request("GET", "https://huggingface.co")))

    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    with pytest.raises(ValueError, match="is not in"):
        driver.build_pipeline(_pipeline(tmp_path, {"model_settings": QP}))
    assert built == []


def test_a_file_missing_from_the_offline_cache_stays_transient(
    tmp_path, built, monkeypatch
):
    """The same miss as any other model's under HF_HUB_OFFLINE=1: an OSError,
    which main.py retries, never a ValueError."""

    def download(repo_id, filename, *, revision=None, **_):
        raise LocalEntryNotFoundError("not in the cache")

    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    with pytest.raises(LocalEntryNotFoundError) as excinfo:
        driver.build_pipeline(_pipeline(tmp_path, {"model_settings": QP}))
    assert isinstance(excinfo.value, OSError)
    assert not isinstance(excinfo.value, ValueError)
    assert built == []
