import pytest

from htrflow_reconciler.parse import (
    PipelineError,
    parse_campaign,
    parse_pipeline,
)

CAMPAIGN = """
pipeline: demo-v1
volumes:
  - R0001203
  - id: dodsbok-1698
    manifest: https://iiif.example.org/xyz/manifest
  - id: loose-scans
    images:
      - https://example.org/scan1.jpg
      - https://example.org/scan2.jpg
"""


def test_parse_campaign_three_forms():
    c = parse_campaign("trolldom", CAMPAIGN)
    assert c.error is None
    assert c.pipeline_id == "demo-v1"
    v = {x.id: x for x in c.volumes}
    assert v["R0001203"].manifest_url == (
        "https://lbiiif.riksarkivet.se/arkis!R0001203/manifest"
    )
    assert v["dodsbok-1698"].manifest_url == "https://iiif.example.org/xyz/manifest"
    assert v["loose-scans"].images == (
        "https://example.org/scan1.jpg",
        "https://example.org/scan2.jpg",
    )


def test_parse_campaign_malformed_yaml_is_contained():
    c = parse_campaign("bad", "pipeline: [unclosed")
    assert c.error is not None
    assert c.volumes == []


def test_parse_campaign_rejects_unsafe_id():
    c = parse_campaign(
        "bad", "pipeline: p\nvolumes:\n  - id: 'a/b'\n    manifest: http://x\n"
    )
    assert c.error is not None and "a/b" in c.error


def test_parse_campaign_rejects_duplicate_ids():
    c = parse_campaign("dup", "pipeline: p\nvolumes:\n  - R1\n  - R1\n")
    assert c.error is not None and "R1" in c.error


PIPELINE = """
image: docker.io/riksarkivet/htrflow-batch@sha256:abc123
steps:
  - step: Segmentation
"""


def test_parse_pipeline_extracts_image_and_steps_hash():
    p = parse_pipeline("demo-v1", PIPELINE)
    assert p.image.endswith("@sha256:abc123")
    assert "Segmentation" in p.steps_yaml
    assert "image:" not in p.steps_yaml  # ConfigMap gets steps only (spec §3)
    assert len(p.steps_sha256) == 64


def test_parse_pipeline_rejects_tag_image():
    with pytest.raises(PipelineError, match="digest"):
        parse_pipeline("demo-v1", "image: repo/img:v5\nsteps: []\n")


def test_parse_pipeline_requires_steps():
    with pytest.raises(PipelineError, match="steps"):
        parse_pipeline("demo-v1", "image: r/i@sha256:a\n")
