"""Rules that live on the pydantic models themselves (spec §3, B63 Task 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from htrflow_converter import parse
from htrflow_converter.models import Campaign, Pipeline, Volume


def test_bare_string_volume_expands_with_source_template_from_context():
    v = Volume.model_validate(
        "R123", context={"source_template": "https://example.org/{ref}/manifest"}
    )
    assert v.id == "R123"
    assert v.manifest == "https://example.org/R123/manifest"
    assert v.images == []


@pytest.mark.parametrize("bad_window", [0, True])
def test_campaign_window_rejected_with_window_in_message(bad_window):
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c", "pipeline": "p", "window": bad_window})
    assert any("window" in str(e["msg"]) for e in exc_info.value.errors())


def test_pipeline_image_outside_allowed_repos_rejected_via_context():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {
                "id": "p",
                "image": "ghcr.io/evil/x@sha256:" + "a" * 64,
                "steps": [{"step": "Segmentation"}],
            },
            context={"allowed_image_repos": ["ghcr.io/riksarkivet"]},
        )
    assert any(
        "not under an allowed repository" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_pipeline_missing_image_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate({"id": "p", "steps": [{"step": "Segmentation"}]})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("image",) and e["type"] == "missing" for e in errors)


def test_pipeline_invalid_image_still_gets_the_old_message():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "repo/img:v5", "steps": [{"step": "Segmentation"}]}
        )
    assert any(
        "image must be digest-pinned" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_pipeline_missing_steps_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate({"id": "p", "image": "ghcr.io/x/y@sha256:" + "a" * 64})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("steps",) and e["type"] == "missing" for e in errors)


def test_pipeline_steps_present_but_not_a_list_still_gets_the_old_message():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "ghcr.io/x/y@sha256:" + "a" * 64, "steps": "nope"}
        )
    assert any("missing steps" in str(e["msg"]) for e in exc_info.value.errors())


def test_campaign_missing_pipeline_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c"})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("pipeline",) and e["type"] == "missing" for e in errors)


def test_campaign_empty_pipeline_still_gets_the_old_message():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c", "pipeline": ""})
    assert any(
        "campaign needs pipeline:" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_filename_wins_over_a_name_or_id_key_in_the_yaml(tmp_path):
    """`path.stem` is the campaign name / pipeline id, always -- a `name:` or
    `id:` key inside the YAML itself is silently ignored, matching the old
    hand-rolled parser (which never read either key)."""
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "real-campaign.yaml").write_text(
        "name: bogus-name\npipeline: real-pipeline\nvolumes:\n  - R1\n"
    )
    (tmp_path / "pipelines" / "real-pipeline.yaml").write_text(
        "id: bogus-id\n"
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\n"
        "steps:\n  - step: Segmentation\n"
    )
    campaigns, pipelines, _ = parse.load(
        tmp_path / "campaigns", tmp_path / "pipelines", tmp_path / "converter.yaml"
    )
    assert campaigns[0].name == "real-campaign"
    assert list(pipelines) == ["real-pipeline"]
