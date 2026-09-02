"""Rules that live on the pydantic models themselves (spec §3, B63 Task 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_campaign_duplicate_volume_ids_rejected():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate(
            {
                "name": "c",
                "pipeline": "p",
                "volumes": [
                    {"id": "R1", "manifest": "https://example.org/a"},
                    {"id": "R1", "manifest": "https://example.org/b"},
                ],
            }
        )
    assert any("duplicate volume id" in str(e["msg"]) for e in exc_info.value.errors())


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
