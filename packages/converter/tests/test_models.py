"""Rules that live on the pydantic models themselves (spec §3, B63 Task 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from htrflow_converter import models, parse
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
    assert any(
        "must be a whole number of 1 or more" in str(e["msg"])
        for e in exc_info.value.errors()
    )


def test_pipeline_missing_image_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate({"id": "p", "steps": [{"step": "Segmentation"}]})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("image",) and e["type"] == "missing" for e in errors)


def test_pipeline_invalid_image_says_what_to_write_instead():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "repo/img:v5", "steps": [{"step": "Segmentation"}]}
        )
    assert any(
        "is not pinned to a digest" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_pipeline_missing_steps_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate({"id": "p", "image": "ghcr.io/x/y@sha256:" + "a" * 64})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("steps",) and e["type"] == "missing" for e in errors)


def test_pipeline_steps_present_but_not_a_list_says_what_a_step_looks_like():
    with pytest.raises(ValidationError) as exc_info:
        Pipeline.model_validate(
            {"id": "p", "image": "ghcr.io/x/y@sha256:" + "a" * 64, "steps": "nope"}
        )
    assert any(
        "must be a list of steps" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_campaign_missing_pipeline_key_is_a_plain_field_required_error():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c"})
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("pipeline",) and e["type"] == "missing" for e in errors)


def test_campaign_empty_pipeline_points_at_the_pipelines_directory():
    with pytest.raises(ValidationError) as exc_info:
        Campaign.model_validate({"name": "c", "pipeline": ""})
    assert any(
        "name one of the files in pipelines/" in str(e["msg"])
        for e in exc_info.value.errors()
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


# -- the images: separator (2026-09-14) ------------------------------------


def test_images_source_line_joins_on_a_space_so_a_iiif_size_comma_survives():
    """A IIIF Image API size segment is a legal comma inside a URL
    (`/full/2500,/0/default.jpg`), so comma can never be the separator: the
    line is `<id>\\t images:<url> <url>`, space-joined."""
    v = Volume(
        id="R0001203",
        images=[
            "https://lbiiif.riksarkivet.se/arkis!R0001203_00044/full/2500,/0/default.jpg",
            "https://lbiiif.riksarkivet.se/arkis!R0001203_00045/full/2500,/0/default.jpg",
        ],
    )
    assert v.source_line() == (
        "R0001203\timages:"
        "https://lbiiif.riksarkivet.se/arkis!R0001203_00044/full/2500,/0/default.jpg "
        "https://lbiiif.riksarkivet.se/arkis!R0001203_00045/full/2500,/0/default.jpg"
    )


@pytest.mark.parametrize("ws", [" ", "\t", "\n", "\r"])
def test_an_image_url_with_whitespace_is_refused_and_says_to_percent_encode_it(ws):
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate({"id": "v1", "images": [f"https://x/a{ws}b.jpg"]})
    assert any(
        "percent-encode it as %20" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_a_manifest_url_with_whitespace_is_refused_the_same_way():
    with pytest.raises(ValidationError) as exc_info:
        Volume.model_validate({"id": "v1", "manifest": "https://x/a b/manifest"})
    assert any(
        "percent-encode it as %20" in str(e["msg"]) for e in exc_info.value.errors()
    )


def test_the_whitespace_problem_names_the_volume_and_the_entry(tmp_path):
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "campaigns" / "kyrk.yaml").write_text(
        "pipeline: demo-v1\nvolumes:\n  - R1\n  - R2\n"
        "  - id: R3\n    images:\n      - 'https://x/a b.jpg'\n"
    )
    (tmp_path / "pipelines" / "demo-v1.yaml").write_text(
        "image: ghcr.io/x/y@sha256:" + "a" * 64 + "\nsteps:\n  - step: Segmentation\n"
    )
    with pytest.raises(parse.ValidationError) as exc_info:
        parse.load(
            tmp_path / "campaigns", tmp_path / "pipelines", tmp_path / "converter.yaml"
        )
    (problem,) = exc_info.value.problems
    assert problem.startswith("campaigns/kyrk.yaml: volume 3 ")
    assert "image 1" in problem and "percent-encode it as %20" in problem


IMAGES_CASES = [
    "https://x/1.jpg https://x/2.jpg",
    "  https://x/1.jpg \t https://x/2.jpg\n",
    "https://x/1.jpg,https://x/2.jpg",
    "https://x/full/2500,/0/default.jpg",
    "https://x/full/2500,/0/default.jpg https://x/2.jpg",
    "https://x/1.jpg",
    "",
]


@pytest.mark.parametrize("value", IMAGES_CASES)
def test_the_converter_and_the_wrapper_split_images_identically(value):
    """The two packages share no code -- the wrapper's image must not carry
    the converter's Kubernetes client -- so the one rule they both implement
    is pinned here instead. A drift is a volume that renders one way and runs
    another."""
    config = pytest.importorskip("htrflow_batch.config")
    assert models.split_image_urls(value) == (
        config.Config.model_construct(images=value).image_urls
    )
