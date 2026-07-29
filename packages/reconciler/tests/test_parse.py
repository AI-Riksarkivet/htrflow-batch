import json

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


def _one_volume(vid: str) -> str:
    # json.dumps gives a YAML double-quoted scalar, so "\n" really is a newline.
    return f"pipeline: p\nvolumes:\n  - id: {json.dumps(vid)}\n    manifest: http://x\n"


@pytest.mark.parametrize(
    "vid",
    ["abc\n", ".hidden", "trailing-", "-leading", "a" * 64, "a/b", ""],
)
def test_parse_campaign_rejects_bad_volume_ids(vid):
    c = parse_campaign("bad", _one_volume(vid))
    assert c.error is not None and "volume id" in c.error


@pytest.mark.parametrize(
    "vid", ["R0001203", "dodsbok-1698", "loose-scans", "a", "a.b_c-d9", "a" * 63]
)
def test_parse_campaign_accepts_good_volume_ids(vid):
    c = parse_campaign("ok", _one_volume(vid))
    assert c.error is None
    assert [v.id for v in c.volumes] == [vid]


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


@pytest.mark.parametrize(
    "pid", ["Demo-v1", "demo_v1", "demo-v1\n", "-demo", "demo-", "d" * 64, ""]
)
def test_parse_pipeline_rejects_non_dns1123_id(pid):
    # ConfigMap names are DNS-1123 labels, so uppercase/underscores are out.
    with pytest.raises(PipelineError, match="unsafe pipeline id"):
        parse_pipeline(pid, PIPELINE)


def test_parse_pipeline_accepts_dns1123_id():
    assert parse_pipeline("demo-v1", PIPELINE).id == "demo-v1"
